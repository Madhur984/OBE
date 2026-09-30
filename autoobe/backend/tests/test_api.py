"""API-layer tests: validation, error shape, and framework-awareness."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_health_declares_no_ai_in_attainment_path():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ai_in_attainment_path"] is False


def test_frameworks_expose_both_po_counts():
    d = client.get("/api/frameworks").json()
    counts = {f["code"]: f["po_count"] for f in d["frameworks"]}
    assert counts == {"GAPC_V3": 12, "GAPC_V4": 11}
    assert d["default"] == "GAPC_V4"


class TestCrosswalkEndpoint:
    def test_merge_is_visible(self):
        assert client.get("/api/crosswalk/PO7").json()["maps_to"] == ["PO6"]
        assert client.get("/api/crosswalk/PO6").json()["maps_to"] == ["PO6"]

    def test_shift(self):
        assert client.get("/api/crosswalk/PO12").json()["maps_to"] == ["PO11"]

    def test_invalid_code_is_400_not_500(self):
        assert client.get("/api/crosswalk/PO99").status_code == 400


class TestCOQuality:
    def test_flags_unmeasurable_statement(self):
        d = client.post("/api/co-quality", json={
            "co_code": "CO1", "statement": "Understand modulation techniques.",
        }).json()
        assert d["is_usable"] is False

    def test_accepts_measurable_statement(self):
        d = client.post("/api/co-quality", json={
            "co_code": "CO1",
            "statement": "Compare digital modulation techniques based on bandwidth efficiency.",
            "blooms_levels": [4],
        }).json()
        assert d["is_usable"] is True
        assert d["detected_verb"] == "compare"


class TestMapping:
    def test_rule2_applied(self):
        d = client.post("/api/map", json={
            "framework_code": "GAPC_V4",
            "co_blooms": {"CO1": [6]},
            "competency": {"CO1": {"PO3": "strong"}},
        }).json()
        assert d["levels"]["CO1"]["PO3"] == 3

    def test_po12_rejected_under_v4(self):
        """The dual-framework guard: v4 has no PO12."""
        r = client.post("/api/map", json={
            "framework_code": "GAPC_V4",
            "co_blooms": {"CO1": [3]},
            "competency": {"CO1": {"PO12": "strong"}},
        })
        assert r.status_code == 400
        assert "11 POs" in r.json()["detail"]

    def test_po12_accepted_under_v3(self):
        r = client.post("/api/map", json={
            "framework_code": "GAPC_V3",
            "co_blooms": {"CO1": [3]},
            "competency": {"CO1": {"PO12": "strong"}},
        })
        assert r.status_code == 200

    def test_strong_match_without_bloom_is_rejected(self):
        r = client.post("/api/map", json={
            "framework_code": "GAPC_V4", "co_blooms": {},
            "competency": {"CO1": {"PO1": "strong"}},
        })
        assert r.status_code == 400


class TestAttainment:
    BASE = {
        "framework_code": "GAPC_V4", "course_code": "C1", "course_name": "Test",
        "questions": [{"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
                       "student_marks": {"a": 8, "b": 4, "c": 9, "d": 6}}],
        "co_po_levels": {"CO1": {"PO1": 3}},
    }

    def test_computes_and_returns_sar_tables(self):
        d = client.post("/api/attainment", json=self.BASE).json()
        assert d["co_attainment"][0]["co_code"] == "CO1"
        assert d["tables"]["course_articulation_matrix"]["number"] == "1.4.2.1"
        assert d["tables"]["direct_attainment"]["number"] == "3.8.1"

    def test_column_count_follows_framework(self):
        v4 = client.post("/api/attainment", json=self.BASE).json()
        v3 = client.post("/api/attainment", json={**self.BASE, "framework_code": "GAPC_V3"}).json()
        # headers = 1 label column + N PO columns
        assert len(v4["tables"]["course_articulation_matrix"]["headers"]) == 12
        assert len(v3["tables"]["course_articulation_matrix"]["headers"]) == 13

    def test_target_modes_differ_on_the_same_data(self):
        fixed = client.post("/api/attainment", json=self.BASE).json()
        avg = client.post("/api/attainment",
                          json={**self.BASE, "target_mode": "class_average"}).json()
        assert (fixed["co_attainment"][0]["cie_success_pct"]
                != avg["co_attainment"][0]["cie_success_pct"])

    def test_mark_above_max_is_400_with_a_useful_message(self):
        r = client.post("/api/attainment", json={
            **self.BASE,
            "questions": [{"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
                           "student_marks": {"a": 99}}],
        })
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "Q1" in detail and "99" in detail  # names the question and the value

    def test_no_questions_is_400(self):
        r = client.post("/api/attainment", json={**self.BASE, "questions": []})
        assert r.status_code == 400

    def test_bad_blend_weights_are_400(self):
        r = client.post("/api/attainment", json={
            **self.BASE, "see_appeared": 4, "see_meeting_target": 2,
            "w_cie": 0.5, "w_see": 0.9,
        })
        assert r.status_code == 400

    def test_unaddressed_po_serialises_as_null_not_zero(self):
        d = client.post("/api/attainment", json=self.BASE).json()
        assert d["course_po_attainment"]["PO11"] is None


def test_ui_is_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "AutoOBE" in r.text


class TestProgramRollup:
    BODY = {
        "framework_code": "GAPC_V4",
        "course_po_attainment": {
            "C1": {"PO1": 2.5, "PO2": 1.2},
            "C2": {"PO1": 1.5, "PO2": None},
        },
        "course_matrices": {"C1": {"CO1": {"PO1": 3, "PO2": 2}}},
    }

    def test_averages_across_courses(self):
        d = client.post("/api/program-rollup", json=self.BODY).json()
        assert d["direct_attainment"]["PO1"] == 2.0   # (2.5+1.5)/2
        assert d["direct_attainment"]["PO2"] == 1.2   # only C1 addresses it

    def test_reports_unaddressed_pos(self):
        d = client.post("/api/program-rollup", json=self.BODY).json()
        assert "PO11" in d["unaddressed_pos"]

    def test_po_cqi_lives_here_not_on_the_course_call(self):
        d = client.post("/api/program-rollup", json=self.BODY).json()
        assert all(r["scope"] == "PO" for r in d["cqi"]["records"])
        assert any(r["code"] == "PO2" for r in d["cqi"]["records"])

    def test_course_call_returns_only_co_scope_cqi(self):
        d = client.post("/api/attainment", json=TestAttainment.BASE).json()
        assert all(r["scope"] == "CO" for r in d["cqi"]["records"])

    def test_orphan_po_flagged_in_validation(self):
        d = client.post("/api/program-rollup", json=self.BODY).json()
        assert d["validation"] is not None
        assert any(v["rule"] == "orphan_po" for v in d["validation"]["violations"])

    def test_table_1_3_2_lists_courses_per_po(self):
        d = client.post("/api/program-rollup", json=self.BODY).json()
        assert d["table_1_3_2"]["PO1"] == ["C1"]

    def test_empty_program_is_400(self):
        r = client.post("/api/program-rollup",
                        json={**self.BODY, "course_po_attainment": {}})
        assert r.status_code == 400
