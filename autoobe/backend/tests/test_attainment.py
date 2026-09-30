"""
Attainment engine regression tests with hand-computed expected values.

Every expected number here was worked out by hand from the SAR's stated
method so the suite pins real arithmetic, not whatever the code happens to
produce. This is the Phase 1 exit-gate evidence.
"""
from __future__ import annotations

import pytest

from app.engine.attainment import (
    AttainmentThresholds, QuestionMarks, TargetMode, blend_direct, blend_overall,
    compute_co_attainment_cie, compute_co_attainment_see, compute_po_attainment_for_course,
    compute_po_direct_attainment, compute_target_marks, evaluate_question, level_for,
)

TH = AttainmentThresholds(t1=60.0, t2=70.0, t3=80.0)


def q(no: str, co: str, mx: float, marks: dict[str, float]) -> QuestionMarks:
    return QuestionMarks(question_no=no, co_code=co, max_marks=mx, student_marks=marks)


class TestTargetModes:
    def test_fixed_threshold(self):
        # 60% of a 10-mark question = 6.0
        assert compute_target_marks(
            q("1", "CO1", 10, {"a": 5}), TargetMode.FIXED_THRESHOLD, 60.0
        ) == 6.0

    def test_class_average(self):
        # mean of 4, 6, 8, 10 = 7.0  (SAR 3.7.2's alternative definition)
        marks = {"a": 4.0, "b": 6.0, "c": 8.0, "d": 10.0}
        assert compute_target_marks(
            q("1", "CO1", 10, marks), TargetMode.CLASS_AVERAGE, 60.0
        ) == 7.0

    def test_modes_give_different_results_on_the_same_data(self):
        """The two modes are materially different -- this is why both must ship."""
        marks = {"a": 4.0, "b": 6.0, "c": 8.0, "d": 10.0}
        question = q("1", "CO1", 10, marks)
        fixed = evaluate_question(question, TargetMode.FIXED_THRESHOLD, 60.0)
        classavg = evaluate_question(question, TargetMode.CLASS_AVERAGE, 60.0)
        assert fixed.passed_pct == 75.0     # 6,8,10 clear 6.0
        assert classavg.passed_pct == 50.0  # only 8,10 clear 7.0

    def test_class_average_needs_students(self):
        with pytest.raises(ValueError, match="at least one present student"):
            compute_target_marks(q("1", "CO1", 10, {}), TargetMode.CLASS_AVERAGE, 60.0)


class TestMarksWeighting:
    def test_weighted_not_flat_mean(self):
        """
        A 2-mark question must not count as much as a 15-mark one.
        Q1: 2 marks, 100% pass.  Q2: 15 marks, 0% pass.
        Flat mean would be 50%; marks-weighted is 2*100/(17) = 11.76%.
        """
        questions = [
            q("1", "CO1", 2, {"a": 2.0, "b": 2.0}),   # both clear 60% of 2 = 1.2
            q("2", "CO1", 15, {"a": 0.0, "b": 0.0}),  # neither clears 9.0
        ]
        res = compute_co_attainment_cie(
            questions, mode=TargetMode.FIXED_THRESHOLD, target_pct=60.0, thresholds=TH
        )
        assert res["CO1"].success_pct == pytest.approx(2 * 100.0 / 17.0)
        assert res["CO1"].success_pct == pytest.approx(11.7647, abs=1e-4)
        assert res["CO1"].level == 0

    def test_multiple_cos_are_independent(self):
        questions = [
            q("1", "CO1", 10, {"a": 10.0, "b": 10.0}),  # 100%
            q("2", "CO2", 10, {"a": 0.0, "b": 0.0}),    # 0%
        ]
        res = compute_co_attainment_cie(
            questions, mode=TargetMode.FIXED_THRESHOLD, target_pct=60.0, thresholds=TH
        )
        assert res["CO1"].success_pct == 100.0 and res["CO1"].level == 3
        assert res["CO2"].success_pct == 0.0 and res["CO2"].level == 0


class TestLevels:
    @pytest.mark.parametrize("pct,expected", [
        (100.0, 3), (80.0, 3), (79.9, 2), (70.0, 2), (69.9, 1), (60.0, 1), (59.9, 0), (0.0, 0),
    ])
    def test_threshold_boundaries(self, pct, expected):
        assert level_for(pct, TH) == expected


class TestSEE:
    def test_uniform_distribution_across_cos(self):
        res = compute_co_attainment_see(
            students_meeting_target=45, students_appeared=60,
            co_codes=["CO1", "CO2", "CO3"], thresholds=TH,
        )
        assert len(res) == 3
        for co in res.values():
            assert co.success_pct == 75.0
            assert co.level == 2

    def test_rejects_impossible_counts(self):
        with pytest.raises(ValueError):
            compute_co_attainment_see(
                students_meeting_target=70, students_appeared=60,
                co_codes=["CO1"], thresholds=TH,
            )


class TestBlending:
    def test_direct_blend(self):
        assert blend_direct(3.0, 2.0, w_cie=0.3, w_see=0.7) == pytest.approx(2.3)

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="must equal 1.0"):
            blend_direct(3.0, 2.0, w_cie=0.5, w_see=0.7)
        with pytest.raises(ValueError, match="must equal 1.0"):
            blend_overall(3.0, 2.0, w_direct=0.9, w_indirect=0.2)

    def test_overall_blend(self):
        assert blend_overall(2.5, 3.0, w_direct=0.8, w_indirect=0.2) == pytest.approx(2.6)


class TestPORollup:
    def test_weighted_rollup_within_a_course(self):
        # CO1=3.0 at weight 3, CO2=1.0 at weight 1  ->  (9+1)/4 = 2.5
        res = compute_po_attainment_for_course(
            {"CO1": 3.0, "CO2": 1.0},
            {"CO1": {"PO1": 3}, "CO2": {"PO1": 1}},
            ["PO1", "PO2"],
        )
        assert res["PO1"] == pytest.approx(2.5)
        assert res["PO2"] is None  # unaddressed, not zero

    def test_unaddressed_po_is_none_not_zero(self):
        """Table 3.8.1 must distinguish 'not addressed' from 'attained nothing'."""
        res = compute_po_attainment_for_course(
            {"CO1": 3.0}, {"CO1": {"PO1": 3}}, ["PO1", "PO11"]
        )
        assert res["PO11"] is None
        assert res["PO11"] != 0.0

    def test_program_level_is_plain_average_across_courses(self):
        """SAR 3.8.1 verbatim: average across all courses addressing that PO."""
        per_course = {
            "C101": {"PO1": 3.0, "PO2": None},
            "C102": {"PO1": 2.0, "PO2": 1.0},
            "C103": {"PO1": 1.0, "PO2": None},
        }
        res = compute_po_direct_attainment(per_course, ["PO1", "PO2", "PO3"])
        assert res["PO1"] == pytest.approx(2.0)   # (3+2+1)/3
        assert res["PO2"] == pytest.approx(1.0)   # only C102 addresses it
        assert res["PO3"] is None

    def test_average_ignores_courses_that_do_not_address_the_po(self):
        """A non-addressing course must not drag the average toward zero."""
        per_course = {"C1": {"PO1": 3.0}, "C2": {"PO1": None}}
        assert compute_po_direct_attainment(per_course, ["PO1"])["PO1"] == pytest.approx(3.0)


class TestValidation:
    def test_mark_above_max_rejected(self):
        with pytest.raises(ValueError, match="outside"):
            q("1", "CO1", 10, {"a": 11.0})

    def test_negative_mark_rejected(self):
        with pytest.raises(ValueError, match="outside"):
            q("1", "CO1", 10, {"a": -1.0})

    def test_zero_max_marks_rejected(self):
        with pytest.raises(ValueError, match="max_marks must be > 0"):
            q("1", "CO1", 0, {"a": 0.0})

    def test_bad_thresholds_rejected(self):
        with pytest.raises(ValueError):
            AttainmentThresholds(t1=80.0, t2=70.0, t3=60.0)


class TestBothFrameworksComputeCorrectly:
    """
    Section 3.4 acceptance criterion: the same attainment machinery must work
    against 12-PO and 11-PO column sets without modification.
    """
    def test_twelve_po_course(self):
        from app.engine.frameworks import get_framework
        po_codes = list(get_framework("GAPC_V3").po_codes)
        res = compute_po_attainment_for_course(
            {"CO1": 3.0}, {"CO1": {"PO12": 2}}, po_codes
        )
        assert len(res) == 12
        assert res["PO12"] == pytest.approx(3.0)

    def test_eleven_po_course(self):
        from app.engine.frameworks import get_framework
        po_codes = list(get_framework("GAPC_V4").po_codes)
        res = compute_po_attainment_for_course(
            {"CO1": 3.0}, {"CO1": {"PO11": 2}}, po_codes
        )
        assert len(res) == 11
        assert res["PO11"] == pytest.approx(3.0)
        assert "PO12" not in res
