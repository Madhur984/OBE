"""
Internal CO attainment, checked against the department's own sheet.

`CO Attainment 2023-24 even.xlsx` is the specification for this calculation,
so the test that matters is not "does the arithmetic look right" but "does it
reproduce all eleven subjects across three semesters, cell for cell". It does,
and the numbers below were copied out of their file rather than out of a run
of this code.

The rest of the file is the edge cases their sheet happens to contain -- an
absent student, a debarred one, a mark stored as 10.799999999999999 -- each of
which moves a published figure if it is read wrongly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.co_attainment import (
    AttainmentError, compute_co_attainment, parse_mark, read_marks_table,
)

FIXTURE = Path(__file__).parent / "fixtures" / "co_attainment_2023-24.xlsx"

# Straight out of their workbook's "Internal CO Attainment" row.
THEIRS = {
    "BEC401": 2.52508658, "BEC403": 2.493411587, "BEC402": 2.387290211,
    "BCC401": 3.0, "BAS401": 3.0,
    "KEC603": 2.040973312, "KEC601": 2.857534247, "KEC063": 2.728535078,
    "KEC602": 2.68014553, "KNC602": 1.821075497,
    "KHU801": 2.341337907,
}


def _blocks():
    if not FIXTURE.exists():
        pytest.skip("the department's attainment workbook is not in fixtures")
    import openpyxl                                        # noqa: PLC0415

    wb = openpyxl.load_workbook(FIXTURE, data_only=True)
    out = []
    for sheet in wb.sheetnames:
        rows = [[c.value for c in r] for r in wb[sheet].iter_rows()]
        try:
            out.extend(read_marks_table(rows))
        except AttainmentError:
            continue
    return out


# ------------------------------------------------------- the marks reader

def test_the_sheet_is_read_as_eleven_subjects():
    blocks = _blocks()
    codes = [b["course_code"] for b in blocks]
    assert set(codes) == set(THEIRS), f"read {codes}"
    # Five COs each, and their real class sizes -- 176, 149 and 187.
    assert all(len(b["max_marks"]) == 5 for b in blocks)
    assert {b["students"] for b in blocks} == {176, 149, 187}


def test_the_summary_rows_are_never_read_as_students():
    """
    The five rows under the students are full of numbers -- the appeared
    count, T1, the crossing count, the percentage and the attainment all sit
    in the CO columns. Reading them as six more people put 182 students in a
    class of 176 and moved every published figure in the sheet.
    """
    block = next(b for b in _blocks() if b["course_code"] == "BEC401")
    assert block["students"] == 176
    assert not any(n.lower().startswith(("no. of", "taget", "target", "% of",
                                         "co attainment", "internal"))
                   for n in block["names"])


# ------------------------------------------------------- the calculation

@pytest.mark.parametrize("code", sorted(THEIRS))
def test_every_subject_reproduces_the_departments_own_figure(code):
    block = next(b for b in _blocks() if b["course_code"] == code)
    got = compute_co_attainment(block["marks"], block["max_marks"],
                                course_code=code, students=block["students"])
    assert got.internal == pytest.approx(THEIRS[code], abs=0.005)


def test_the_five_summary_rows_of_one_subject_match_column_for_column():
    """BEC401, against the numbers printed in their sheet."""
    block = next(b for b in _blocks() if b["course_code"] == "BEC401")
    got = compute_co_attainment(block["marks"], block["max_marks"])
    want = [  # appeared, T1, crossing, %, attainment
        (135, 11.93, 95, 70.37, 3.00),
        (135, 10.57, 72, 53.33, 2.67),
        (176, 6.30, 78, 44.32, 2.22),
        (175, 6.30, 93, 53.14, 2.66),
        (175, 6.30, 73, 41.71, 2.09),
    ]
    for row, (app, t1, cross, pct, att) in zip(got.cos, want):
        assert row.appeared == app, row.co_code
        assert row.target_marks == pytest.approx(t1, abs=0.01), row.co_code
        assert row.crossing == cross, row.co_code
        assert row.crossing_pct == pytest.approx(pct, abs=0.01), row.co_code
        assert row.attainment == pytest.approx(att, abs=0.01), row.co_code


def test_a_mark_a_hair_under_the_target_still_counts():
    """
    One mark in their sheet is stored as 10.799999999999999 against a T1 of
    10.8. Excel counts that student as having crossed; an exact float
    comparison does not, and the difference is a whole student. Both sides are
    rounded to two places, which is what makes this reproduce their file.
    """
    got = compute_co_attainment(
        {"CO1": [10.799999999999999, 0.0]}, {"CO1": 24.0}, t1_pct=45.0)
    assert got.cos[0].crossing == 1


# --------------------------------------------------- absent and debarred

@pytest.mark.parametrize("marker", ["AB", "ab", "DB", "db", "D", "", None, "  "])
def test_a_student_who_did_not_sit_is_not_a_zero(marker):
    assert parse_mark(marker) is None


def test_absent_students_leave_the_denominator_rather_than_deflating_it():
    """
    Their sheet has 135 students appearing for CO1 of a subject and 176 for
    CO3 of the same subject. Counting an absence as nought would have put the
    attainment of every course a sick student missed below what it is.
    """
    present = compute_co_attainment({"CO1": [10, 10, "AB", "DB"]}, {"CO1": 20})
    zeros = compute_co_attainment({"CO1": [10, 10, 0, 0]}, {"CO1": 20})
    assert present.cos[0].appeared == 2 and present.cos[0].crossing_pct == 100.0
    assert zeros.cos[0].appeared == 4 and zeros.cos[0].crossing_pct == 50.0
    assert present.cos[0].attainment == 3.0
    assert zeros.cos[0].attainment == pytest.approx(2.5)


def test_a_co_nobody_sat_is_reported_rather_than_dropped():
    got = compute_co_attainment({"CO1": [10], "CO2": ["AB", "AB"]},
                                {"CO1": 20, "CO2": 20})
    assert [c.co_code for c in got.cos] == ["CO1", "CO2"]
    assert got.cos[1].appeared == 0 and got.cos[1].attainment == 0.0


# ------------------------------------------------------------ the targets

def test_both_targets_move_the_answer_and_are_the_teachers_to_set():
    marks = {"CO1": [18, 16, 12, 8, 4]}          # of 20
    # T1 45% -> 9 marks: four of five cross -> 80% -> capped at 3
    assert compute_co_attainment(marks, {"CO1": 20}).cos[0].attainment == 3.0
    # T1 80% -> 16 marks: two cross -> 40% -> 40/60*3 = 2.0
    at = compute_co_attainment(marks, {"CO1": 20}, t1_pct=80.0)
    assert at.cos[0].crossing == 2
    assert at.cos[0].attainment == pytest.approx(2.0)
    # A stricter T2 lowers the same cohort's attainment.
    strict = compute_co_attainment(marks, {"CO1": 20}, t1_pct=80.0, t2_pct=100.0)
    assert strict.cos[0].attainment == pytest.approx(1.2)


def test_attainment_never_exceeds_three():
    got = compute_co_attainment({"CO1": [20, 20, 20]}, {"CO1": 20})
    assert got.cos[0].crossing_pct == 100.0
    assert got.cos[0].attainment == 3.0


@pytest.mark.parametrize("t1,t2", [(0, 60), (-5, 60), (101, 60), (45, 0), (45, 101)])
def test_an_impossible_target_is_refused(t1, t2):
    with pytest.raises(AttainmentError):
        compute_co_attainment({"CO1": [1]}, {"CO1": 10}, t1_pct=t1, t2_pct=t2)


def test_marks_above_the_maximum_are_flagged_not_silently_accepted():
    got = compute_co_attainment({"CO1": [25, 10]}, {"CO1": 20})
    assert got.ignored and "above the maximum" in got.ignored[0]


def test_a_table_with_no_co_columns_says_so():
    with pytest.raises(AttainmentError, match="CO columns"):
        read_marks_table([["Name", "Marks"], ["A", 10]])
