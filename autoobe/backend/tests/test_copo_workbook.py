"""
Reproducing the department's own CO-PO/PSO workbook, block for block.

The department supplied "All CO-PO Mapping 2024-25.xlsx" — 44 course blocks
across three sheets, hand-built — and asked for output that looks like it.
Every assertion below is written against a fact measured from that file, not
against a guess at what a mapping sheet ought to look like:

  * the corner cell of a block is the COURSE, "Title (CODE)", not "CO"
  * the columns are PO1..PO12 then PSO1, PSO2
  * a level is 1, 2 or 3; an unmapped cell is EMPTY — never 0, never "-"
  * the last row of a block repeats the course label and holds the averages
  * an average divides by the COs that map to that column, not by every CO
    in the course (23 discriminating cases in their file, 0 counter-examples)
"""
from __future__ import annotations

import io

import pytest

from app.engine.copo_document import (
    convention_note, course_block_table, course_label,
)
from app.engine.office_export import ExportUnavailable, course_blocks_to_xlsx

openpyxl = pytest.importorskip("openpyxl")

COLS = [f"PO{i}" for i in range(1, 13)] + ["PSO1", "PSO2"]

# BEC-302, verbatim from the department's sheet (rows 24-30 of "CO-PO Mapping 2nd").
DSD_COS = [
    {"code": "CO1", "statement": "Perform numerous arithmetic and logic simplification using various methods."},
    {"code": "CO2", "statement": "Design and analyze modular combinational circuits with MUX / DEMUX, Decoder & Encoder."},
    {"code": "CO3", "statement": "Design & analyze synchronous sequential logic circuits."},
    {"code": "CO4", "statement": "Analyze various logic families and design circuits using PLDs."},
    {"code": "CO5", "statement": "Design various ADCs and DACs according to the given specifications."},
]
DSD_LEVELS = {
    "CO1": {"PO1": 3, "PO2": 3, "PO3": 2, "PO4": 3, "PO5": 3, "PO12": 3, "PSO1": 3, "PSO2": 2},
    "CO2": {"PO1": 3, "PO2": 3, "PO3": 3, "PO4": 3, "PO5": 3, "PO12": 3, "PSO1": 3, "PSO2": 3},
    "CO3": {"PO1": 3, "PO2": 3, "PO3": 3, "PO4": 3, "PO5": 3, "PO12": 3, "PSO1": 3, "PSO2": 3},
    "CO4": {"PO1": 3, "PO2": 3, "PO3": 3, "PO4": 3, "PO5": 3, "PO12": 3, "PSO1": 3, "PSO2": 3},
    "CO5": {"PO1": 3, "PO2": 3, "PO3": 3, "PO4": 3, "PO5": 3, "PO12": 3, "PSO1": 3, "PSO2": 3},
}


def _block(**kw):
    return course_block_table(DSD_COS, DSD_LEVELS, codes=COLS,
                              course_code="BEC-302",
                              course_title="Digital System Design", **kw)


def test_the_course_is_the_corner_cell():
    assert course_label("BEC-302", "Digital System Design") == \
        "Digital System Design (BEC-302)"
    # and it degrades rather than printing an empty bracket
    assert course_label("BEC-302", "") == "BEC-302"
    assert course_label("", "Digital System Design") == "Digital System Design"
    assert course_label("", "") == "Course"


def test_the_block_has_the_shape_of_the_departments_sheet():
    t = _block()
    assert t.headers == ["Digital System Design (BEC-302)"] + COLS
    assert len(t.rows) == len(DSD_COS) + 1
    # last row repeats the course label and is marked as the summary
    assert t.rows[-1][0] == "Digital System Design (BEC-302)"
    assert t.summary_rows == (len(t.rows) - 1,)


def test_an_unmapped_cell_is_empty_not_zero():
    """
    0 reads as a scored zero and "-" is this codebase's own convention, not
    theirs. The department's cell is blank.
    """
    t = _block()
    row = t.rows[0]
    for col, value in zip(COLS, row[1:]):
        if col in DSD_LEVELS["CO1"]:
            assert value == str(DSD_LEVELS["CO1"][col])
        else:
            assert value == "", f"{col} rendered {value!r}, expected blank"


def test_the_matrix_row_reproduces_the_sheet_exactly():
    """Row 25 of "CO-PO Mapping 2nd", cell for cell."""
    t = _block(co_prefix="C204")
    assert t.rows[0] == [
        "C204.1 Perform numerous arithmetic and logic simplification using various methods.",
        "3", "3", "2", "3", "3", "", "", "", "", "", "", "3", "3", "2",
    ]


def test_the_average_row_reproduces_the_sheet_exactly():
    """Row 30 of the same sheet: PO3 averages 2.8, PSO2 averages 2.8."""
    t = _block()
    avg = t.rows[-1]
    assert avg[1:6] == ["3.00", "3.00", "2.80", "3.00", "3.00"]
    assert avg[6:12] == [""] * 6          # untouched columns stay blank
    assert avg[12:] == ["3.00", "3.00", "2.80"]


def test_the_average_divides_by_the_cos_that_map():
    """
    The discriminating case, taken from their "Project KEC-753" block: four
    COs map at 3, one does not map at all. Their average is 3, not 2.4.
    """
    cos = [{"code": f"CO{i}", "statement": "x"} for i in range(1, 6)]
    levels = {"CO1": {"PO1": 3}, "CO2": {"PO1": 3}, "CO3": {"PO1": 3},
              "CO4": {}, "CO5": {"PO1": 3}}
    t = course_block_table(cos, levels, codes=["PO1"], course_code="KEC-753")
    assert t.rows[-1][1] == "3.00"


def test_the_department_numbering_is_supplied_not_guessed():
    """
    "C204.1" carries the department's own course serial. Nothing in a
    syllabus PDF says 204, so it is passed in; without it the CO's own code
    from the syllabus is used rather than a fabricated number.
    """
    assert _block(co_prefix="C204").rows[0][0].startswith("C204.1 ")
    assert _block(co_prefix="C204").rows[4][0].startswith("C204.5 ")
    assert _block().rows[0][0].startswith("CO1 ")


def test_a_course_block_header_is_the_bare_po_code():
    """
    The block reproduces the department's own sheet, so its header row is
    theirs: PO1..PO12, nothing appended. A column filled as departmental
    policy is not annotated here -- it is named once, in the method note,
    which `test_the_policy_columns_are_named_in_the_method_note` covers.
    """
    t = _block(convention_codes=("PO12",))
    assert t.headers[1:] == COLS
    assert not any("†" in h for h in t.headers)


def test_the_policy_columns_are_named_in_the_method_note():
    note = convention_note(("PO1", "PO12"))
    assert "PO1, PO12" in note
    assert "Rule 1 and Rule 2" in note      # the level is still derived


# ------------------------------------------------------------- the workbook

def _load(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data), data_only=True)


def test_many_blocks_stack_down_one_sheet():
    blocks = [_block(number=str(i)) for i in range(1, 4)]
    wb = _load(course_blocks_to_xlsx({"CO-PO Mapping 2nd": blocks}))
    assert wb.sheetnames == ["Cover", "CO-PO Mapping 2nd"]
    ws = wb["CO-PO Mapping 2nd"]
    # 3 blocks x (1 header + 5 COs + 1 average), with a spacer row between
    # them. The trailing spacer holds nothing, so it is not a used row.
    assert ws.max_row == 3 * 7 + 2
    assert ws.cell(row=1, column=1).value == "Digital System Design (BEC-302)"
    assert ws.cell(row=7, column=1).value == "Digital System Design (BEC-302)"
    assert ws.cell(row=8, column=1).value is None      # the spacer
    assert ws.cell(row=9, column=1).value == "Digital System Design (BEC-302)"
    # only the label column is frozen: freezing rows would pin whichever
    # course happened to come first, which means nothing on a stacked sheet
    assert ws.freeze_panes == "B1"


def test_a_level_is_written_as_a_whole_number():
    """
    A mapping level is 1, 2 or 3. The SAR writer coerces every value to float
    and stamps 0.00, which prints a level as "3.00" — wrong for a matrix cell
    and right for the average two rows below it.
    """
    ws = _load(course_blocks_to_xlsx({"m": [_block()]}))["m"]
    cell = ws.cell(row=2, column=2)               # CO1 x PO1
    assert cell.value == 3 and isinstance(cell.value, int)
    assert cell.number_format == "0"
    avg = ws.cell(row=7, column=4)                # average x PO3
    assert avg.value == pytest.approx(2.8)
    assert isinstance(avg.value, float)


def test_an_unmapped_cell_is_genuinely_empty_in_the_file():
    ws = _load(course_blocks_to_xlsx({"m": [_block()]}))["m"]
    assert ws.cell(row=2, column=7).value is None      # CO1 x PO6
    assert ws.cell(row=7, column=7).value is None      # its average


def test_the_whole_programme_goes_out_as_one_file():
    sheets = {"CO-PO Mapping 2nd": [_block()],
              "CO-PO Mapping 3rd": [_block(), _block()],
              "CO-PO Mapping 4th": [_block()]}
    wb = _load(course_blocks_to_xlsx(sheets))
    assert wb.sheetnames == ["Cover", "CO-PO Mapping 2nd",
                             "CO-PO Mapping 3rd", "CO-PO Mapping 4th"]
    cover = "\n".join(str(c.value) for row in wb["Cover"].iter_rows()
                      for c in row if c.value)
    assert "CO-PO Mapping 3rd" in cover and "2 course block(s)" in cover


def test_sheet_names_that_collide_after_truncation_do_not_overwrite():
    long_a = "CO-PO Mapping for the second year of the programme A"
    long_b = "CO-PO Mapping for the second year of the programme B"
    wb = _load(course_blocks_to_xlsx({long_a: [_block()], long_b: [_block()]}))
    assert len(wb.sheetnames) == 3, wb.sheetnames
    assert len(set(wb.sheetnames)) == 3


def test_the_notes_reach_the_cover():
    wb = _load(course_blocks_to_xlsx({"m": [_block()]},
                                     notes=["Averages divide by mapped COs."]))
    text = "\n".join(str(c.value) for row in wb["Cover"].iter_rows()
                     for c in row if c.value)
    assert "Averages divide by mapped COs." in text


def test_the_writer_says_what_is_missing_rather_than_failing_late():
    import app.engine.office_export as oe

    if oe._XLSX:
        pytest.skip("openpyxl is installed")
    with pytest.raises(ExportUnavailable, match="openpyxl"):
        course_blocks_to_xlsx({"m": [_block()]})


def test_a_column_no_course_touches_is_named_in_the_workbook():
    """
    Filling only the columns an outcome evidences is the honest answer to
    "does this outcome teach that". It is the wrong answer to "does this
    programme cover that", which only the whole file can answer — so the
    workbook says which columns came out empty across every block.
    """
    from fastapi.testclient import TestClient
    from app.api.main import app

    client = TestClient(app)
    r = client.post("/api/copo-workbook", json={
        "framework_code": "GAPC_V3",
        "courses": [{
            "course_code": "BEC302", "course_title": "Digital System Design",
            "cos": [{"code": "CO1", "statement": "Design various ADCs and DACs."}],
            "co_levels": {"CO1": {"PO1": 3, "PO3": 3, "PO12": 3}},
        }],
    })
    assert r.status_code == 200
    wb = _load(r.content)
    cover = "\n".join(str(c.value or "") for row in wb["Cover"].iter_rows()
                      for c in row)
    assert "Addressed by no course outcome anywhere in this workbook" in cover
    assert "PO4" in cover and "PO5" in cover
    # and the columns that were used are not listed as gaps
    gap_line = next(l for l in cover.splitlines()
                    if "Addressed by no course outcome" in l)
    for used in ("PO1,", "PO3,", "PO12,"):
        assert used not in gap_line
