"""
Tests for the automation layer: competency inference, constraints, the CO
rewriter, marks ingestion, and CQI drafting.
"""
from __future__ import annotations

import pytest

from app.engine.co_rewriter import rewrite_all, rewrite_co
from app.engine.competency import Competency, propose_for_co, propose_matrix
from app.engine.constraints import (
    Severity, po_course_coverage, validate_course_matrix, validate_program_matrix,
)
from app.engine.cqi import GapSeverity, generate_co_cqi, generate_po_cqi, summarise
from app.engine.ingest import detect_outliers, ingest_marks, to_question_payload
from app.seed.curriculum import AKTU_RAW_SAMPLES, BY_CODE, list_courses


class TestCompetencyInference:
    def _po(self, statement, blooms=None, fw="GAPC_V4"):
        return {p.po_code: p.competency
                for p in propose_for_co(statement, framework_code=fw, blooms_levels=blooms)}

    def test_design_outcome_hits_design_po(self):
        p = self._po("Design a digital communication system to satisfy specified "
                     "performance requirements.", [6])
        assert p.get("PO3") is Competency.STRONG

    def test_analysis_outcome_hits_problem_analysis(self):
        p = self._po("Analyze the performance of digital modulation techniques.", [4])
        assert p.get("PO2") is Competency.STRONG

    def test_tool_outcome_hits_tool_usage(self):
        p = self._po("Demonstrate transaction control using a modern database tool.", [3])
        assert p.get("PO5") in (Competency.STRONG, Competency.PARTIAL)

    def test_sustainability_outcome_hits_engineer_and_world(self):
        p = self._po("Evaluate solid waste management practices against sustainability "
                     "and whole-life cost criteria.", [5])
        assert p.get("PO6") is Competency.STRONG

    def test_ethics_outcome_hits_ethics_po(self):
        p = self._po("Apply environmental legislation and ethical principles to "
                     "engineering practice.", [3])
        assert p.get("PO7") is Competency.STRONG

    def test_teamwork_and_communication(self):
        p = self._po("Function effectively as a member of a team and manage the project "
                     "schedule and cost.", [3])
        assert p.get("PO8") is Competency.STRONG
        assert p.get("PO10") in (Competency.STRONG, Competency.PARTIAL)

    def test_recall_outcome_cannot_strongly_claim_design(self):
        """The hard ceiling: a K1/K2 outcome must not claim strong design."""
        p = self._po("Explain the design of combinational circuits.", [2])
        assert p.get("PO3") is not Competency.STRONG

    def test_same_concept_lands_on_different_po_codes_per_framework(self):
        """Ethics is PO8 under v3 and PO7 under v4 — the merge shifted it."""
        stmt = "Apply professional ethics and human values to engineering practice."
        v3 = self._po(stmt, [3], fw="GAPC_V3")
        v4 = self._po(stmt, [3], fw="GAPC_V4")
        assert v3.get("PO8") is Competency.STRONG
        assert v4.get("PO7") is Competency.STRONG

    def test_unrelated_text_proposes_little(self):
        p = self._po("The quick brown fox jumps over the lazy dog.", [1])
        assert len(p) <= 1

    def test_matrix_caps_pos_per_co(self):
        m = propose_matrix(
            {"CO1": ("Design and analyze and evaluate systems using modern tools for "
                     "society, environment, ethics, teams, communication, cost and "
                     "lifelong learning.", [6])},
            framework_code="GAPC_V4", max_pos_per_co=6,
        )
        assert len(m["CO1"]) <= 6

    def test_proposals_are_deterministic(self):
        a = propose_for_co("Analyze network protocols.", framework_code="GAPC_V4", blooms_levels=[4])
        b = propose_for_co("Analyze network protocols.", framework_code="GAPC_V4", blooms_levels=[4])
        assert [(p.po_code, p.score) for p in a] == [(p.po_code, p.score) for p in b]

    def test_evidence_names_matched_terms(self):
        p = propose_for_co("Design a control system.", framework_code="GAPC_V4",
                           blooms_levels=[6])
        assert any("design" in t for t in p[0].matched_terms)


class TestConstraints:
    def test_too_many_pos_is_an_error(self):
        m = {"CO1": {f"PO{i}": 2 for i in range(1, 9)}}
        r = validate_course_matrix(m, framework_code="GAPC_V4")
        assert not r.is_valid
        assert any(v.rule == "max_pos_per_co" for v in r.errors)

    def test_co_mapping_nothing_is_an_error(self):
        r = validate_course_matrix({"CO1": {}}, framework_code="GAPC_V4")
        assert any(v.rule == "orphan_co" for v in r.errors)

    def test_too_many_threes_warns(self):
        r = validate_course_matrix({"CO1": {"PO1": 3, "PO2": 3, "PO3": 3}},
                                   framework_code="GAPC_V4")
        assert any(v.rule == "max_level3_per_co" for v in r.warnings)

    def test_sparse_po_overuse_warns(self):
        m = {f"CO{i}": {"PO6": 3} for i in range(1, 5)}
        r = validate_course_matrix(m, framework_code="GAPC_V4")
        assert any(v.rule == "sparse_po" for v in r.warnings)

    def test_home_po_is_exempt_from_column_mean(self):
        m = {f"CO{i}": {"PO7": 3} for i in range(1, 5)}
        with_exempt = validate_course_matrix(m, framework_code="GAPC_V4", home_pos=("PO7",))
        without = validate_course_matrix(m, framework_code="GAPC_V4")
        assert len(with_exempt.violations) < len(without.violations)

    def test_clean_matrix_passes(self):
        m = {"CO1": {"PO1": 3, "PO2": 2}, "CO2": {"PO2": 3, "PO3": 2}}
        assert validate_course_matrix(m, framework_code="GAPC_V4").is_valid

    def test_orphan_po_is_a_program_level_error(self):
        r = validate_program_matrix(
            {"C1": {"CO1": {"PO1": 3}}}, framework_code="GAPC_V4",
            po_codes=["PO1", "PO2"],
        )
        assert not r.is_valid
        assert any(v.rule == "orphan_po" and "PO2" in v.cells for v in r.errors)

    def test_orphan_pso_is_an_error(self):
        r = validate_program_matrix(
            {"C1": {"CO1": {"PO1": 3}}}, framework_code="GAPC_V4",
            po_codes=["PO1"], pso_codes=["PSO1"],
        )
        assert any(v.rule == "orphan_pso" for v in r.errors)

    def test_po_course_coverage_feeds_table_132(self):
        cov = po_course_coverage(
            {"C1": {"CO1": {"PO1": 3}}, "C2": {"CO1": {"PO1": 1, "PO2": 2}}},
            ["PO1", "PO2", "PO3"],
        )
        assert cov["PO1"] == ["C1", "C2"]
        assert cov["PO2"] == ["C2"]
        assert cov["PO3"] == []


class TestCORewriter:
    @pytest.mark.parametrize("raw", AKTU_RAW_SAMPLES)
    def test_real_aktu_statements_become_usable(self, raw):
        from app.engine.co_quality import check_co
        r = rewrite_co("CO1", raw)
        assert r.changed
        assert check_co("CO1", r.proposed, declared_blooms_levels=r.blooms_levels).is_usable

    def test_strips_teacher_intent_opener(self):
        r = rewrite_co("CO1", "To provide an understanding of network protocols")
        assert not r.proposed.lower().startswith("to provide")
        assert r.proposed.endswith(".")

    def test_compliant_statement_is_left_alone(self):
        stmt = "Analyze the performance of digital modulation techniques."
        r = rewrite_co("CO1", stmt, declared_blooms_levels=[4])
        assert r.already_compliant and not r.changed
        assert r.proposed == stmt

    def test_declared_bloom_level_drives_the_verb(self):
        r = rewrite_co("CO1", "Student should be aware of control systems",
                       declared_blooms_levels=[6])
        assert r.proposed.split()[0] == "Design"

    def test_original_is_always_retained(self):
        raw = "To provide hardware issues in distributed systems"
        r = rewrite_co("CO1", raw)
        assert r.original == raw
        assert "provenance" in r.note

    def test_bundled_verbs_are_flagged_not_silently_split(self):
        r = rewrite_co("CO1", "Explain, analyze, design and implement communication systems.")
        assert r.split_suggestion
        assert "," in r.proposed or "and" in r.proposed  # text itself not mangled

    def test_empty_statement_yields_a_template(self):
        r = rewrite_co("CO1", "")
        assert "<subject matter>" in r.proposed

    def test_rewrite_all_returns_every_co(self):
        out = rewrite_all({"CO1": ("Understand X", [2]), "CO2": ("Analyze Y.", [4])})
        assert set(out) == {"CO1", "CO2"}


class TestIngest:
    def test_detects_tab_separated_with_header(self):
        r = ingest_marks("Roll\tQ1\tQ2\n21CS001\t8\t9\n21CS002\t7\t6")
        assert r.student_count == 2
        assert [c.question_label for c in r.question_columns] == ["Q1", "Q2"]

    def test_detects_csv_without_header(self):
        r = ingest_marks("21CS001,8,9\n21CS002,7,6")
        assert r.student_count == 2
        assert len(r.question_columns) == 2

    def test_handles_sub_part_headers(self):
        r = ingest_marks("Roll No.,Q1(a),Q.1b,2(a)\n21CS001,4,3,5")
        assert [c.question_label for c in r.question_columns] == ["Q1a", "Q1b", "Q2a"]

    def test_ignores_name_and_total_columns(self):
        r = ingest_marks("Roll,Name,Q1,Total\n21CS001,Asha,8,8")
        labels = [c.question_label for c in r.question_columns]
        assert labels == ["Q1"]

    def test_drops_trailing_total_row(self):
        r = ingest_marks("Roll,Q1\n21CS001,8\nTotal,8")
        assert r.student_count == 1
        assert any("Total" in s for s in r.skipped_rows)

    def test_flags_duplicate_rolls(self):
        r = ingest_marks("Roll,Q1\n21CS001,8\n21CS001,7")
        assert r.student_count == 1
        assert any("duplicate" in s for s in r.skipped_rows)

    def test_absent_marker_is_excluded_not_zeroed(self):
        r = ingest_marks("Roll,Q1\n21CS001,AB\n21CS002,8")
        payload, _ = to_question_payload(
            r, co_by_question={"Q1": "CO1"}, max_by_question={"Q1": 10}
        )
        assert "21CS001" not in payload[0]["student_marks"]
        assert payload[0]["student_marks"]["21CS002"] == 8.0

    def test_unreadable_cell_is_reported(self):
        r = ingest_marks("Roll,Q1\n21CS001,eight")
        _, problems = to_question_payload(
            r, co_by_question={"Q1": "CO1"}, max_by_question={"Q1": 10}
        )
        assert problems and "eight" in problems[0]

    def test_warns_when_no_roll_column_recognised(self):
        r = ingest_marks("A,B\n1,2\n3,4")
        assert r.warnings

    def test_outlier_detection_flags_likely_entry_error(self):
        payload = [
            {"question_no": "Q1", "max_marks": 10, "student_marks": {"r1": 9}},
            {"question_no": "Q2", "max_marks": 10, "student_marks": {"r1": 0}},
            {"question_no": "Q3", "max_marks": 10, "student_marks": {"r1": 10}},
        ]
        flags = detect_outliers(payload)
        assert flags and "Q2" in flags[0]

    def test_consistent_low_scorer_is_not_flagged(self):
        payload = [
            {"question_no": f"Q{i}", "max_marks": 10, "student_marks": {"r1": 2}}
            for i in range(1, 4)
        ]
        assert detect_outliers(payload) == []


class TestCQI:
    def test_record_created_only_below_target(self):
        recs = generate_co_cqi({"CO1": 1.2, "CO2": 2.5}, target=2.0)
        assert [r.code for r in recs] == ["CO1"]

    def test_zero_attainment_is_critical(self):
        recs = generate_co_cqi({"CO1": 0.0}, target=2.0)
        assert recs[0].severity is GapSeverity.CRITICAL

    def test_small_gap_is_minor(self):
        recs = generate_co_cqi({"CO1": 1.6}, target=2.0)
        assert recs[0].severity is GapSeverity.MINOR

    def test_action_cites_the_weakest_questions(self):
        recs = generate_co_cqi(
            {"CO1": 1.0}, target=2.0,
            question_detail={"CO1": [("Q3", 22.0, 15.0), ("Q1", 91.0, 10.0)]},
        )
        assert "Q3" in recs[0].drafted_action
        assert "Q1" not in recs[0].drafted_action  # strong question not blamed

    def test_po_records_skip_unaddressed_pos(self):
        recs = generate_po_cqi({"PO1": 1.0, "PO2": None}, target=2.0)
        assert [r.code for r in recs] == ["PO1"]

    def test_summary_counts_by_severity(self):
        recs = generate_co_cqi({"CO1": 0.0, "CO2": 0.9, "CO3": 1.7}, target=2.0)
        s = summarise(recs)
        assert s["total"] == 3 and s["critical"] == 1


class TestCurriculumLibrary:
    def test_every_course_has_cos_in_the_valid_range(self):
        for c in list_courses():
            assert 3 <= len(c.cos) <= 6, f"{c.code} has {len(c.cos)} COs"

    def test_every_co_carries_bloom_levels(self):
        for c in list_courses():
            for co in c.cos:
                assert co.blooms_levels, f"{c.code}/{co.code} has no Bloom level"
                assert all(1 <= b <= 6 for b in co.blooms_levels)

    def test_library_cos_are_already_compliant(self):
        """The shipped corpus must not need rewriting — it is the good example."""
        from app.engine.co_quality import check_co
        for c in list_courses():
            for co in c.cos:
                r = check_co(co.code, co.statement, declared_blooms_levels=co.blooms_levels)
                assert r.is_usable, f"{c.code}/{co.code}: {[f.message for f in r.findings]}"

    def test_ltp_to_ncrf_conversion(self):
        c = BY_CODE["KCS501"]           # L-T-P 3-1-0, 4 credits
        h = c.ncrf_hours
        assert h["total"] == 120        # 4 credits x 30 hours
        assert h["CI"] == 60            # (3+1) x 15 weeks
        assert h["LI"] == 0
        assert h["CI"] + h["LI"] + h["TW_SL"] == h["total"]

    def test_lab_course_has_lab_hours(self):
        h = BY_CODE["KCS551"].ncrf_hours   # 0-0-2, 1 credit
        assert h["LI"] == 30 and h["CI"] == 0

    def test_raw_samples_are_genuinely_non_compliant(self):
        from app.engine.co_quality import check_co
        for raw in AKTU_RAW_SAMPLES:
            assert not check_co("CO1", raw).is_usable


class TestEndToEndAutomation:
    def test_library_course_flows_through_the_whole_pipeline(self):
        """A course from the library should map and validate without human input."""
        course = BY_CODE["KEC601"]
        cos = {co.code: (co.statement, co.blooms_levels) for co in course.cos}
        matrix = propose_matrix(cos, framework_code="GAPC_V4")
        assert all(matrix[co] for co in cos), "every outcome should map somewhere"

        from app.engine.frameworks import get_framework
        from app.engine.mapping_rules import map_co_to_po
        fw = get_framework("GAPC_V4")
        levels = {}
        for co, pos in matrix.items():
            levels[co] = {}
            for po, comp in pos.items():
                res = map_co_to_po(
                    po_code=po, competency=Competency(comp),
                    co_blooms_levels=cos[co][1], po_action_level=fw.po_action_levels[po],
                )
                if res.level:
                    levels[co][po] = res.level

        report = validate_course_matrix(
            levels, framework_code="GAPC_V4", home_pos=course.home_pos
        )
        assert report.is_valid, [v.message for v in report.errors]
