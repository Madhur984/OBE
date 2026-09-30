"""
The three gaps that made the SAR incomplete rather than merely manual:
PSO columns, indirect assessment, and choice-question bias.
"""
from __future__ import annotations

import pytest

from app.engine.attainment import (
    AttainmentThresholds, AttemptPolicy, QuestionMarks, TargetMode,
    choice_bias_report, compute_co_attainment_cie, evaluate_question,
)
from app.engine import indirect as ind
from app.engine import pso as P
from app.engine.mapping_rules import Competency


# ---------------------------------------------------------------------------
# PSOs
# ---------------------------------------------------------------------------

CSE_PSOS = [
    P.PSO("PSO1", "Apply data structures, algorithms and database concepts to "
                  "develop efficient software applications."),
    P.PSO("PSO2", "Design and deploy networked and cloud-based systems using "
                  "current programming platforms and security practices."),
]


def test_a_pso_that_restates_a_po_is_rejected():
    """The single most common peer-team finding about PSOs."""
    r = P.check_pso("PSO1", "Apply engineering knowledge to solve complex "
                            "engineering problems in society.")
    assert not r.is_usable
    assert any("restatement of a Program Outcome" in f.message for f in r.findings)


def test_a_genuinely_specific_pso_passes():
    r = P.check_pso("PSO1", CSE_PSOS[0].statement)
    assert r.is_usable, [f.message for f in r.findings]
    assert r.specificity > P.SPECIFICITY_FLOOR
    assert "algorithm" in " ".join(r.distinctive_terms)


def test_near_duplicate_psos_are_flagged():
    a = "Design and develop database applications using SQL and modern tools."
    b = "Develop and design database applications with SQL using modern tools."
    r = P.check_pso("PSO1", a, siblings=[b])
    assert any(f.check == "Distinct" for f in r.findings)


def test_missing_pso_set_is_an_error_not_a_warning():
    _, set_findings = P.check_pso_set([])
    assert any(f.severity == "error" for f in set_findings)
    assert "automatic finding" in set_findings[0].message


def test_pso_set_size_is_advisory():
    _, findings = P.check_pso_set(CSE_PSOS)
    assert all(f.severity != "error" for f in findings)


def test_co_maps_to_the_pso_it_actually_matches():
    props = P.propose_co_pso(
        "Construct SQL queries to retrieve and manipulate data from a "
        "relational database.",
        CSE_PSOS, blooms_levels=[3],
    )
    assert props, "A database CO must map to the database PSO."
    assert props[0].pso_code == "PSO1"


def test_networking_co_maps_to_the_networking_pso():
    props = P.propose_co_pso(
        "Design secure networked systems using current cloud platforms.",
        CSE_PSOS, blooms_levels=[6],
    )
    assert props[0].pso_code == "PSO2"


def test_unrelated_co_maps_to_no_pso():
    props = P.propose_co_pso(
        "Explain the laws of thermodynamics and entropy principles.",
        CSE_PSOS, blooms_levels=[2],
    )
    assert not props, "An unrelated outcome must propose nothing, not a weak guess."


def test_recall_level_co_cannot_claim_strong_on_a_design_pso():
    props = P.propose_co_pso(
        "Define networked cloud systems, programming platforms and security "
        "practices.",
        CSE_PSOS, blooms_levels=[1],
    )
    for p in props:
        assert p.competency is not Competency.STRONG


def test_pso_matrix_runs_through_the_same_rule_engine():
    cos = {
        "CO1": ("Construct SQL queries to manipulate data in a relational "
                "database.", [3]),
        "CO2": ("Design secure cloud-based networked systems.", [6]),
    }
    mappings = P.propose_pso_matrix(cos, CSE_PSOS)
    assert mappings
    for m in mappings:
        assert 1 <= m.level <= 3
        assert "Rule" in m.rationale, "PSO levels must be derived, not asserted."
    levels = P.pso_levels_dict(mappings)
    assert set(levels) <= {"CO1", "CO2"}


def test_pso_action_level_comes_from_its_verb():
    assert P.pso_action_level("Design and develop distributed systems.") == 3
    assert P.pso_action_level("Explain the fundamentals of computing.") == 1
    assert P.pso_action_level("No verb here at all.") == 2


def test_empty_pso_set_infers_nothing_rather_than_crashing():
    assert P.propose_co_pso("Anything at all.", []) == []
    assert P.propose_pso_matrix({"CO1": ("x", [2])}, []) == []


# ---------------------------------------------------------------------------
# Indirect assessment
# ---------------------------------------------------------------------------

COS = {
    "CO1": "Explain the architecture of a database management system.",
    "CO2": "Apply entity-relationship modelling to design a schema.",
}


def test_survey_is_generated_from_the_outcomes():
    inst = ind.build_course_exit_survey(COS, course_title="DBMS")
    assert [i.target_code for i in inst.items] == ["CO1", "CO2"]
    assert inst.items[0].text.startswith("I can explain")
    assert "anonymous" in inst.preamble


def test_scale_floor_scores_zero_not_twenty_percent():
    """
    A room that answered 1 to everything has attained nothing. Dividing the
    raw mean by the scale maximum would report 20% and flatter every survey.
    """
    inst = ind.build_course_exit_survey({"CO1": COS["CO1"]})
    r = ind.score_survey(inst, [{"item_CO1": 1} for _ in range(12)])
    assert r.items[0].pct == 0.0
    top = ind.score_survey(inst, [{"item_CO1": 5} for _ in range(12)])
    assert top.items[0].pct == 100.0
    mid = ind.score_survey(inst, [{"item_CO1": 3} for _ in range(12)])
    assert mid.items[0].pct == 50.0


def test_indirect_uses_the_same_thresholds_as_direct():
    inst = ind.build_course_exit_survey({"CO1": COS["CO1"]})
    th = AttainmentThresholds(t1=60, t2=70, t3=80)
    r = ind.score_survey(inst, [{"item_CO1": 5} for _ in range(12)], thresholds=th)
    assert r.items[0].level == 3
    r2 = ind.score_survey(inst, [{"item_CO1": 3} for _ in range(12)], thresholds=th)
    assert r2.items[0].level == 0, "50% is below t1; one ladder, used everywhere."


def test_thin_response_rate_is_reported_not_hidden():
    inst = ind.build_course_exit_survey(COS)
    r = ind.score_survey(inst, [{"item_CO1": 5, "item_CO2": 5} for _ in range(15)],
                         population=120)
    assert r.adequacy == "thin"
    assert any("Self-selection dominates" in w for w in r.warnings)


def test_too_few_responses_is_insufficient():
    inst = ind.build_course_exit_survey(COS)
    r = ind.score_survey(inst, [{"item_CO1": 5, "item_CO2": 4} for _ in range(4)])
    assert r.adequacy == "insufficient"
    assert any("not evidence of anything" in w for w in r.warnings)


def test_healthy_survey_is_adequate():
    inst = ind.build_course_exit_survey(COS)
    r = ind.score_survey(inst, [{"item_CO1": 4, "item_CO2": 4} for _ in range(60)],
                         population=120)
    assert r.adequacy == "adequate"
    assert r.response_rate == 0.5


def test_out_of_range_answers_are_discarded_with_a_warning():
    inst = ind.build_course_exit_survey({"CO1": COS["CO1"]})
    responses = [{"item_CO1": 4} for _ in range(11)] + [{"item_CO1": 9}]
    r = ind.score_survey(inst, responses)
    assert r.items[0].responses == 11
    assert any("outside 1..5" in w for w in r.warnings)


def test_respondent_tokens_are_pseudonymous_and_unlinkable():
    a = ind.respondent_token("survey-1", "21CS001")
    b = ind.respondent_token("survey-2", "21CS001")
    assert "21CS001" not in a
    assert a != b, "The same student must not be linkable across surveys."
    assert a == ind.respondent_token("survey-1", "21CS001"), "Must be stable."


def test_blend_defaults_to_eighty_twenty():
    blended = ind.blend_attainment({"PO1": 2.0}, {"PO1": 3.0})
    assert blended[0].overall == pytest.approx(2.2)
    assert "80% direct" in blended[0].note


def test_missing_indirect_falls_back_to_direct_rather_than_zero():
    """Substituting zero for a survey nobody ran fabricates a number."""
    blended = ind.blend_attainment({"PO1": 2.4}, {})
    assert blended[0].overall == 2.4
    assert blended[0].indirect is None
    assert "no indirect evidence" in blended[0].note


def test_indirect_only_outcome_is_flagged_as_a_finding():
    blended = ind.blend_attainment({}, {"PO7": 2.8})
    assert blended[0].overall == 2.8
    assert "itself a finding" in blended[0].note


def test_unaddressed_outcome_stays_none():
    blended = ind.blend_attainment({"PO1": None}, {"PO1": None})
    assert blended[0].overall is None


def test_blend_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        ind.blend_attainment({"PO1": 2.0}, {"PO1": 3.0}, w_direct=0.9, w_indirect=0.2)


def test_blending_a_percentage_with_a_level_is_refused():
    with pytest.raises(ValueError) as exc:
        ind.blend_attainment({"PO1": 1.78}, {"PO1": 56.25})
    assert "attainment scale" in str(exc.value)
    assert "percentage" in str(exc.value)


def test_percent_converts_to_the_level_scale():
    th = AttainmentThresholds(t1=60, t2=70, t3=80)
    out = ind.to_level_scale({"CO1": 85.0, "CO2": 72.0, "CO3": 40.0, "CO4": None},
                             scale="percent", thresholds=th)
    assert out == {"CO1": 3.0, "CO2": 2.0, "CO3": 0.0, "CO4": None}


def test_levels_pass_through_unchanged():
    assert ind.to_level_scale({"CO1": 2.4}, scale="level") == {"CO1": 2.4}


def test_unknown_scale_is_rejected():
    with pytest.raises(ValueError):
        ind.to_level_scale({"CO1": 2.0}, scale="fraction")


def test_survey_result_exposes_both_units():
    inst = ind.build_course_exit_survey({"CO1": COS["CO1"]})
    r = ind.score_survey(inst, [{"item_CO1": 5} for _ in range(12)])
    assert r.by_target()["CO1"] == 100.0     # percentage
    assert r.levels()["CO1"] == 3            # attainment level


def test_indirect_rolls_up_through_the_articulation_matrix():
    co_indirect = {"CO1": 80.0, "CO2": 40.0}
    weights = {"CO1": {"PO1": 3}, "CO2": {"PO1": 1, "PO2": 3}}
    po = ind.rollup_indirect_to_po(co_indirect, weights, ["PO1", "PO2", "PO3"])
    assert po["PO1"] == pytest.approx((80 * 3 + 40 * 1) / 4)
    assert po["PO2"] == 40.0
    assert po["PO3"] is None, "An unaddressed PO stays None, never 0."


def test_methodology_note_states_the_convention_is_not_nba_s():
    note = ind.methodology_note(w_direct=0.8, w_indirect=0.2, scale_max=5)
    assert "NBA does not prescribe it" in note
    assert "same thresholds as direct" in note


def test_employer_and_alumni_instruments_differ_in_voice():
    outcomes = {"PO1": "Apply engineering knowledge."}
    alum = ind.build_outcome_survey(outcomes, kind="alumni")
    emp = ind.build_outcome_survey(outcomes, kind="employer")
    assert alum.items[0].text.startswith("My program prepared me")
    assert emp.items[0].text.startswith("Our engineers")


# ---------------------------------------------------------------------------
# Choice questions
# ---------------------------------------------------------------------------

TH = AttainmentThresholds()


def _q(no, co, mx, marks, skipped=()):
    return QuestionMarks(no, co, mx, marks, frozenset(skipped),
                         "G1" if skipped else None)


def test_a_mark_and_a_skip_cannot_both_apply_to_one_student():
    with pytest.raises(ValueError) as exc:
        QuestionMarks("Q1", "CO1", 10, {"a": 5}, frozenset({"a"}))
    assert "not attempted" in str(exc.value)


def test_attempted_only_ignores_skippers():
    q = _q("Q1", "CO1", 10, {"a": 8, "b": 9}, skipped=["c", "d"])
    out = evaluate_question(q, TargetMode.FIXED_THRESHOLD, 60,
                            AttemptPolicy.ATTEMPTED_ONLY)
    assert out.students_present == 2
    assert out.passed_pct == 100.0
    assert out.attempt_rate == 0.5


def test_cohort_policy_counts_skippers_as_not_meeting_target():
    q = _q("Q1", "CO1", 10, {"a": 8, "b": 9}, skipped=["c", "d"])
    out = evaluate_question(q, TargetMode.FIXED_THRESHOLD, 60, AttemptPolicy.COHORT)
    assert out.students_present == 4
    assert out.students_meeting_target == 2
    assert out.passed_pct == 50.0


def test_class_average_target_is_taken_over_attempters_even_under_cohort():
    """
    Averaging in scores nobody earned would move the target itself, not just
    the pass count.
    """
    q = _q("Q1", "CO1", 10, {"a": 4, "b": 8}, skipped=["c", "d"])
    lenient = evaluate_question(q, TargetMode.CLASS_AVERAGE, 0,
                                AttemptPolicy.ATTEMPTED_ONLY)
    strict = evaluate_question(q, TargetMode.CLASS_AVERAGE, 0, AttemptPolicy.COHORT)
    assert lenient.target_marks == strict.target_marks == 6.0


def test_compulsory_paper_produces_no_bias_report():
    qs = [QuestionMarks("Q1", "CO1", 10, {"a": 8, "b": 4})]
    assert choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                              target_pct=60, thresholds=TH) == []


def test_bias_report_quantifies_the_spread():
    qs = [_q("Q1", "CO1", 10, {"a": 9, "b": 8}, skipped=["c", "d", "e"])]
    report = choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                                target_pct=60, thresholds=TH)
    assert len(report) == 1
    b = report[0]
    assert b.attempted_only_pct == 100.0
    assert b.cohort_pct == 40.0
    assert b.gap == 60.0
    assert b.attempt_rate == pytest.approx(0.4)


def test_co_evidenced_only_by_optional_questions_is_an_error():
    qs = [_q("Q1", "CO1", 10, {"a": 9, "b": 8}, skipped=["c", "d", "e"])]
    b = choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                           target_pct=60, thresholds=TH)[0]
    assert b.all_evidence_optional
    assert b.severity == "error"
    assert "self-selected group" in b.message


def test_co_with_one_compulsory_question_is_only_a_warning():
    qs = [
        QuestionMarks("Q1", "CO1", 10, {"a": 9, "b": 8, "c": 3, "d": 2, "e": 1}),
        _q("Q2", "CO1", 10, {"a": 9, "b": 8}, skipped=["c", "d", "e"]),
    ]
    b = choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                           target_pct=60, thresholds=TH)[0]
    assert not b.all_evidence_optional
    assert b.severity in ("warning", "info")


def test_near_universal_attempt_reports_no_material_difference():
    qs = [_q("Q1", "CO1", 10, {f"s{i}": 8 for i in range(20)}, skipped=[])]
    assert choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                              target_pct=60, thresholds=TH) == []


def test_policy_flows_through_co_attainment():
    qs = [_q("Q1", "CO1", 10, {"a": 9, "b": 8}, skipped=["c", "d"])]
    lenient = compute_co_attainment_cie(qs, mode=TargetMode.FIXED_THRESHOLD,
                                        target_pct=60, thresholds=TH)
    strict = compute_co_attainment_cie(qs, mode=TargetMode.FIXED_THRESHOLD,
                                       target_pct=60, thresholds=TH,
                                       policy=AttemptPolicy.COHORT)
    assert lenient["CO1"].success_pct == 100.0
    assert strict["CO1"].success_pct == 50.0
    assert lenient["CO1"].evidence_is_optional


def test_default_policy_is_unchanged_for_existing_callers():
    """Adding choice handling must not move any existing number."""
    qs = [QuestionMarks("Q1", "CO1", 10, {"a": 8, "b": 4, "c": 9})]
    out = compute_co_attainment_cie(qs, mode=TargetMode.FIXED_THRESHOLD,
                                    target_pct=60, thresholds=TH)
    assert out["CO1"].success_pct == pytest.approx(200 / 3)
