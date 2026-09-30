"""API surface for PSOs, surveys, trends, migration, exports, audit, approvals."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)

CSE_PSOS = [
    {"code": "PSO1", "statement": "Apply data structures, algorithms and database "
                                  "concepts to develop efficient software applications."},
    {"code": "PSO2", "statement": "Design and deploy networked and cloud-based systems "
                                  "using current programming platforms and security "
                                  "practices."},
]
COS = {
    "CO1": {"statement": "Construct SQL queries to retrieve and manipulate data from a "
                         "relational database.", "blooms_levels": [3]},
    "CO2": {"statement": "Design secure cloud-based networked systems using current "
                         "platforms.", "blooms_levels": [6]},
}


# ------------------------------------------------------------------ PSOs

def test_pso_check_rejects_a_restated_po():
    r = client.post("/api/pso/check", json={"psos": [
        {"code": "PSO1", "statement": "Apply engineering knowledge to solve complex "
                                      "engineering problems in society."}]})
    assert r.status_code == 200
    body = r.json()
    assert body["usable"] is False
    assert any(f["severity"] == "error" for f in body["reports"][0]["findings"])


def test_pso_check_accepts_a_specific_set():
    r = client.post("/api/pso/check", json={"psos": CSE_PSOS})
    assert r.json()["usable"] is True


def test_empty_pso_set_is_an_error_at_the_api():
    body = client.post("/api/pso/check", json={"psos": []}).json()
    assert any(f["severity"] == "error" for f in body["set_findings"])


def test_pso_mapping_is_proposed_not_authored():
    r = client.post("/api/pso/propose", json={"cos": COS, "psos": CSE_PSOS})
    body = r.json()
    assert body["levels"]["CO1"]["PSO1"] >= 1
    assert body["levels"]["CO2"]["PSO2"] >= 1
    assert all("Rule" in m["rationale"] for m in body["mappings"])


def test_pso_propose_reports_unmapped_psos():
    body = client.post("/api/pso/propose", json={
        "cos": {"CO1": {"statement": "Explain the laws of thermodynamics.",
                        "blooms_levels": [2]}},
        "psos": CSE_PSOS}).json()
    assert set(body["unmapped_psos"]) == {"PSO1", "PSO2"}


def test_programs_round_trip_with_psos():
    code = "CSE-PSO-TEST"
    r = client.post("/api/programs", json={"code": code, "name": "B.Tech CSE",
                                           "framework_code": "GAPC_V4"})
    assert r.status_code == 200
    pid = r.json()["id"]

    assert client.put(f"/api/programs/{pid}/psos",
                      json={"psos": CSE_PSOS}).json()["stored"] is True
    listed = client.get("/api/programs").json()["programs"]
    mine = next(p for p in listed if p["id"] == pid)
    assert [p["code"] for p in mine["psos"]] == ["PSO1", "PSO2"]


def test_duplicate_program_is_rejected():
    payload = {"code": "DUP-TEST", "name": "X", "framework_code": "GAPC_V4"}
    client.post("/api/programs", json=payload)
    assert client.post("/api/programs", json=payload).status_code == 409


def test_unknown_framework_is_rejected():
    r = client.post("/api/programs", json={"code": "F-TEST", "name": "X",
                                           "framework_code": "GAPC_V9"})
    assert r.status_code == 400


# --------------------------------------------------------------- surveys

def test_survey_is_generated_from_outcomes():
    r = client.post("/api/survey/build", json={
        "cos": {"CO1": "Explain the architecture of a database management system."},
        "course_title": "DBMS"})
    body = r.json()
    assert body["items"][0]["target_code"] == "CO1"
    assert body["items"][0]["text"].startswith("I can explain")
    assert body["scale_labels"][-1] == "Strongly agree"


def test_survey_scoring_reports_adequacy_and_note():
    inst = client.post("/api/survey/build", json={
        "cos": {"CO1": "Explain the relational model.",
                "CO2": "Apply normalization to a schema."}}).json()
    responses = [{"item_CO1": 4, "item_CO2": 3} for _ in range(60)]
    body = client.post("/api/survey/score", json={
        "instrument": inst, "responses": responses, "population": 120}).json()
    assert body["adequacy"] == "adequate"
    assert body["by_target"]["CO1"] == 75.0
    assert body["by_target"]["CO2"] == 50.0
    assert "NBA does not prescribe it" in body["methodology_note"]


def test_thin_survey_is_labelled_thin():
    inst = client.post("/api/survey/build",
                       json={"cos": {"CO1": "Explain the relational model."}}).json()
    body = client.post("/api/survey/score", json={
        "instrument": inst, "responses": [{"item_CO1": 5}] * 12,
        "population": 200}).json()
    assert body["adequacy"] == "thin"
    assert body["warnings"]


def test_blend_falls_back_to_direct_when_indirect_is_absent():
    body = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 2.4}, "indirect": {}}).json()
    row = body["blended"][0]
    assert row["overall"] == 2.4
    assert "no indirect evidence" in row["note"]


def test_blend_applies_the_weights():
    body = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 2.0}, "indirect": {"PO1": 3.0},
        "w_direct": 0.8, "w_indirect": 0.2}).json()
    assert body["blended"][0]["overall"] == pytest.approx(2.2)


def test_blend_rejects_weights_that_do_not_sum_to_one():
    r = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 2.0}, "indirect": {"PO1": 3.0},
        "w_direct": 0.9, "w_indirect": 0.2})
    assert r.status_code == 400


def test_co_scoped_indirect_rolls_up_through_the_matrix():
    """A survey percentage becomes a level before it is blended, not after."""
    body = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 2.0},
        "indirect": {"CO1": 80.0},          # 80% -> level 3 at the default t3
        "indirect_scale": "percent",
        "indirect_is_co_scoped": True,
        "co_po_levels": {"CO1": {"PO1": 3}},
    }).json()
    row = next(r for r in body["blended"] if r["target_code"] == "PO1")
    assert row["indirect"] == 3.0
    assert row["overall"] == pytest.approx(0.8 * 2.0 + 0.2 * 3.0)


def test_blending_a_percentage_as_a_level_is_refused():
    """
    The bug this guard exists for: direct 1.78 blended with indirect 56.25
    produced "PO1 = 12.67", which looks like an answer and means nothing.
    """
    r = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 1.78}, "indirect": {"PO1": 56.25}})
    assert r.status_code == 400
    assert "attainment scale" in r.json()["detail"]


def test_percent_conversion_uses_the_supplied_thresholds():
    body = client.post("/api/blend-attainment", json={
        "direct": {"PO1": 2.0}, "indirect": {"PO1": 72.0},
        "indirect_scale": "percent", "t1": 50, "t2": 60, "t3": 70,
    }).json()
    assert body["blended"][0]["indirect"] == 3.0


# ---------------------------------------------------------------- trends

def _run(year, values, framework="GAPC_V4"):
    return {"academic_year": year, "created_at": f"{year}-06-01",
            "config": {"framework_code": framework},
            "result": {"po_direct_attainment": values}}


def test_trends_need_history():
    assert client.post("/api/trends", json={"runs": []}).status_code == 400


def test_trends_report_direction_and_gap():
    body = client.post("/api/trends", json={
        "runs": [_run("2024-25", {"PO1": 1.8}), _run("2025-26", {"PO1": 2.4})],
        "targets": {"PO1": 2.0}}).json()
    po1 = next(s for s in body["series"] if s["target_code"] == "PO1")
    assert po1["direction"] == "improving"
    assert po1["meets_target"] is True
    assert body["summary"]["years"] == ["2024-25", "2025-26"]


def test_trends_cross_the_framework_boundary():
    body = client.post("/api/trends", json={"runs": [
        _run("2023-24", {"PO8": 2.2}, framework="GAPC_V3"),
        _run("2025-26", {"PO7": 2.6}),
    ]}).json()
    po7 = next(s for s in body["series"] if s["target_code"] == "PO7")
    assert [p["value"] for p in po7["points"]] == [2.2, 2.6]


def test_closure_evidence_is_returned():
    body = client.post("/api/trends", json={
        "runs": [_run("2023-24", {"PO3": 1.8}), _run("2024-25", {"PO3": 2.4})],
        "actions": [{"target_code": "PO3", "action": "Added a design studio",
                     "academic_year": "2023-24"}]}).json()
    assert body["closure"][0]["verdict"] == "closed"


# ------------------------------------------------------------- migration

LEGACY = {"CO1": {"PO1": 3}, "CO2": {"PO6": 2, "PO7": 3}, "CO3": {"PO8": 3, "PO12": 2}}


def test_migration_shifts_and_merges_correctly():
    body = client.post("/api/migrate-matrix", json={"levels": LEGACY}).json()
    assert body["levels"]["CO2"] == {"PO6": 3}
    assert body["levels"]["CO3"] == {"PO7": 3, "PO11": 2}
    assert body["review_count"] >= 1


def test_migration_with_cos_returns_a_comparison():
    body = client.post("/api/migrate-matrix", json={
        "levels": LEGACY,
        "cos": {"CO1": {"statement": "Explain the architecture of a database "
                                     "management system.", "blooms_levels": [2]},
                "CO2": {"statement": "Analyze the environmental impact of engineering "
                                     "activity on public health.", "blooms_levels": [4]},
                "CO3": {"statement": "Apply ethical principles to engineering "
                                     "practice.", "blooms_levels": [3]}},
    }).json()
    assert "rederived" in body
    assert "recommendation" in body
    assert 0 <= body["agreement_rate"] <= 1


def test_migration_needs_a_matrix():
    assert client.post("/api/migrate-matrix", json={"levels": {}}).status_code == 400


# -------------------------------------------------------- document parsing

PAPER = """SECTION B
2. Attempt any two of the following:
(a) Explain the three-schema architecture of a DBMS. [10]
(b) Draw an ER diagram for a library system. [10]
(c) Write SQL queries for the second highest salary. [10]
"""


def test_question_paper_upload_detects_choice_groups():
    r = client.post("/api/parse-question-paper",
                    files={"file": ("paper.txt", PAPER.encode(), "text/plain")})
    body = r.json()
    assert len(body["questions"]) == 3
    assert all(q["choice_group"] for q in body["questions"])
    assert body["total_marks"] == 20, "Any two of three tens is 20, not 30."


def test_empty_upload_is_rejected():
    r = client.post("/api/parse-question-paper",
                    files={"file": ("x.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_marks_xlsx_upload():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sessional 1"
    ws.append(["Roll No", "Name", "Q1", "Q2", "Total"])
    ws.append(["21CS001", "A", 8, 7, 15])
    ws.append(["21CS002", "B", 6, 9, 15])
    buf = io.BytesIO()
    wb.save(buf)

    r = client.post("/api/parse-marks-file", files={
        "file": ("marks.xlsx", buf.getvalue(),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    body = r.json()
    assert body["student_count"] == 2
    assert body["question_labels"] == ["Q1", "Q2"]
    assert body["sheets"] == ["Sessional 1"]


# ---------------------------------------------------------------- exports

TABLES = [{
    "number": "1.4.2.1", "title": "Course articulation matrix",
    "headers": ["Course Outcomes (COs)", "PO1", "PO2", "PSO1"],
    "rows": [["CO1", "3", "2", "-"], ["CO2", "2", "3", "3"]],
}]


def test_export_formats_are_reported():
    body = client.get("/api/export/formats").json()
    assert set(body) == {"docx", "xlsx"}


def test_docx_export_returns_a_document():
    r = client.post("/api/export/docx", json={"tables": TABLES, "program": "B.Tech CSE"})
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.docx"')


def test_xlsx_export_returns_a_workbook():
    r = client.post("/api/export/xlsx", json={"tables": TABLES,
                                              "filename": "annexures"})
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert 'filename="annexures.xlsx"' in r.headers["content-disposition"]


def test_export_without_tables_is_rejected():
    assert client.post("/api/export/docx", json={"tables": []}).status_code == 400


def test_malformed_table_payload_is_rejected():
    r = client.post("/api/export/docx", json={"tables": [{"number": "1", "title": "x"}]})
    assert r.status_code == 400


# ------------------------------------------------------ audit and approvals

def test_audit_events_chain_and_verify():
    for i in range(3):
        r = client.post("/api/audit",
                        json={"action": f"test.{i}", "entity_type": "course",
                              "entity_id": "audit-chain-test"},
                        headers={"X-Actor": "fac@x.edu", "X-Role": "faculty"})
        assert r.status_code == 200

    body = client.get("/api/audit",
                      params={"entity_id": "audit-chain-test"}).json()
    assert body["chain"]["ok"] is True
    assert [e["action"] for e in body["events"]] == ["test.0", "test.1", "test.2"]
    assert all(e["actor"] == "fac@x.edu" for e in body["events"])


def test_unknown_role_is_rejected():
    r = client.post("/api/audit",
                    json={"action": "x", "entity_type": "course", "entity_id": "c"},
                    headers={"X-Role": "principal"})
    assert r.status_code == 400


def test_approval_chain_over_http():
    eid = "approval-http-test"
    base = f"/api/approvals/course_matrix/{eid}"

    assert client.get(base, headers={"X-Role": "faculty"}).json()["state"] == "draft"

    r = client.post(base, json={"action": "submit"},
                    headers={"X-Actor": "fac@x.edu", "X-Role": "faculty"})
    assert r.json()["state"] == "submitted"

    # Faculty may not approve their own work.
    denied = client.post(base, json={"action": "approve"},
                         headers={"X-Actor": "fac@x.edu", "X-Role": "faculty"})
    assert denied.status_code == 409

    client.post(base, json={"action": "approve"},
                headers={"X-Actor": "hod@x.edu", "X-Role": "hod"})
    locked = client.post(base, json={"action": "lock"},
                         headers={"X-Actor": "nba@x.edu", "X-Role": "coordinator"})
    assert locked.json()["state"] == "locked"

    state = client.get(base, headers={"X-Role": "coordinator"}).json()
    assert state["is_locked"] is True
    assert state["available_actions"] == []
    assert len(state["history"]) == 3


def test_unlock_requires_a_reason_over_http():
    eid = "unlock-http-test"
    base = f"/api/approvals/course_matrix/{eid}"
    for action, role in (("submit", "faculty"), ("approve", "hod"), ("lock", "coordinator")):
        client.post(base, json={"action": action}, headers={"X-Role": role})

    bare = client.post(base, json={"action": "unlock"},
                       headers={"X-Role": "coordinator"})
    assert bare.status_code == 409

    ok = client.post(base, json={"action": "unlock",
                                 "note": "Q3 marks came from the wrong section."},
                     headers={"X-Role": "coordinator"})
    assert ok.json()["state"] == "returned"


def test_evidence_bundle_404s_for_an_unknown_course():
    r = client.get("/api/evidence/nope", params={"academic_year": "2025-26"})
    assert r.status_code == 404


# ------------------------------------------------------------- snapshot

SNAPSHOT = {
    "program_code": "SNAP-CSE", "program_name": "B.Tech CSE", "branch": "CSE",
    "framework_code": "GAPC_V4", "course_code": "SNAP501",
    "course_name": "Database Management Systems", "academic_year": "2025-26",
    "cos": {
        "CO1": {"statement": "Explain the architecture of a DBMS.", "blooms_levels": [2]},
        "CO2": {"statement": "Apply ER modelling to design a schema.", "blooms_levels": [3],
                "original_statement": "Student should be aware of ER modelling"},
    },
    "psos": CSE_PSOS,
    "co_po_levels": {"CO1": {"PO1": 3}, "CO2": {"PO1": 2, "PO3": 3}},
    "co_pso_levels": {"CO2": {"PSO1": 3}},
    "overrides": ["CO2|PO3"],
    "questions": [
        {"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
         "student_marks": {"21CS001": 8, "21CS002": 6},
         "not_attempted": ["21CS003"], "choice_group": "G1"},
    ],
    "config": {"t1": 60, "t2": 70, "t3": 80},
    "result": {"co_attainment": {"CO1": {"success_pct": 50.0, "level": 0}},
               "po_direct_attainment": {"PO1": 1.4}},
}


def test_snapshot_persists_the_whole_course():
    r = client.post("/api/workspace/snapshot", json=SNAPSHOT,
                    headers={"X-Actor": "fac@x.edu", "X-Role": "faculty"})
    assert r.status_code == 200
    ids = r.json()
    assert ids["course_id"] and ids["run_id"]

    ev = client.get(f"/api/evidence/{ids['course_id']}",
                    params={"academic_year": "2025-26"}).json()
    assert [o["code"] for o in ev["outcomes"]] == ["CO1", "CO2"]
    assert ev["result"]["po_direct_attainment"]["PO1"] == 1.4
    assert ev["chain"]["ok"] is True


def test_snapshot_preserves_override_provenance():
    ids = client.post("/api/workspace/snapshot", json=SNAPSHOT).json()
    ev = client.get(f"/api/evidence/{ids['course_id']}",
                    params={"academic_year": "2025-26"}).json()
    by_cell = {(m["co_code"], m["target_code"]): m for m in ev["mapping"]}
    assert by_cell[("CO2", "PO3")]["source"] == "override"
    assert by_cell[("CO1", "PO1")]["source"] == "proposed"


def test_snapshot_stores_psos_alongside_pos():
    ids = client.post("/api/workspace/snapshot", json=SNAPSHOT).json()
    ev = client.get(f"/api/evidence/{ids['course_id']}",
                    params={"academic_year": "2025-26"}).json()
    kinds = {m["target_kind"] for m in ev["mapping"]}
    assert kinds == {"PO", "PSO"}


def test_snapshot_distinguishes_absence_from_non_attempt():
    ids = client.post("/api/workspace/snapshot", json=SNAPSHOT).json()
    ev = client.get(f"/api/evidence/{ids['course_id']}",
                    params={"academic_year": "2025-26"}).json()
    q = ev["assessments"][0]["questions"][0]
    assert q["present"] == 2
    assert q["not_attempted"] == 1


def test_snapshot_is_idempotent_on_the_same_course():
    a = client.post("/api/workspace/snapshot", json=SNAPSHOT).json()
    b = client.post("/api/workspace/snapshot", json=SNAPSHOT).json()
    assert a["course_id"] == b["course_id"], "Re-running must not fork the course."
    assert a["run_id"] != b["run_id"], "Each run is its own record."


def test_snapshot_refuses_to_overwrite_a_locked_course():
    payload = {**SNAPSHOT, "course_code": "SNAP-LOCKED"}
    client.post("/api/workspace/snapshot", json=payload)
    base = "/api/approvals/course_matrix/SNAP-LOCKED"
    for action, role in (("submit", "faculty"), ("approve", "hod"), ("lock", "coordinator")):
        client.post(base, json={"action": action}, headers={"X-Role": role})

    r = client.post("/api/workspace/snapshot", json=payload)
    assert r.status_code == 409
    assert "locked" in r.json()["detail"].lower()


def test_stored_course_runs_roll_up_into_program_trends():
    """
    Persistence has to reach the trend panel or it is just a write-only log.
    Only per-course runs exist, so they are averaged into program years by
    SAR 3.8.1's stated method.
    """
    prog = "SNAP-ROLLUP"
    for year, po1 in (("2024-25", 1.4), ("2025-26", 2.2)):
        client.post("/api/workspace/snapshot", json={
            **SNAPSHOT, "program_code": prog, "course_code": "ROLLUP-A",
            "academic_year": year,
            "result": {"co_attainment": {}, "po_direct_attainment": {"PO1": po1}},
        })
    ids = client.post("/api/workspace/snapshot", json={
        **SNAPSHOT, "program_code": prog, "course_code": "ROLLUP-B",
        "academic_year": "2025-26",
        "result": {"co_attainment": {}, "po_direct_attainment": {"PO1": 2.6}},
    }).json()

    body = client.post("/api/trends", json={
        "runs": [], "program_id": ids["program_id"], "targets": {"PO1": 2.0}}).json()
    po1 = next(s for s in body["series"] if s["target_code"] == "PO1")
    assert [p["academic_year"] for p in po1["points"]] == ["2024-25", "2025-26"]
    assert po1["points"][0]["value"] == pytest.approx(1.4)
    # Two courses in 2025-26: the plain average, per SAR 3.8.1.
    assert po1["points"][1]["value"] == pytest.approx(2.4)
    assert po1["direction"] == "improving"
    assert po1["meets_target"] is True


def test_trends_still_400_when_a_program_has_no_runs():
    r = client.post("/api/programs", json={"code": "EMPTY-PROG", "name": "X"})
    body = client.post("/api/trends", json={"runs": [], "program_id": r.json()["id"]})
    assert body.status_code == 400


# ------------------------------------------------------------ tagger quality

def test_tagger_quality_is_reported_in_product():
    body = client.get("/api/tagger-quality").json()
    assert body["total"] >= 80
    assert 0 <= body["silent_error_rate"] <= 1
    assert "per_course" in body


def test_tagger_quality_can_be_split():
    body = client.get("/api/tagger-quality", params={"split": "holdout"}).json()
    assert body["split"] == "holdout"
    assert body["total"] >= 30


# ------------------------------- attainment: choice questions and PSO columns

def _marks(n, value):
    return {f"21CS{i:03d}": value for i in range(1, n + 1)}


def test_attainment_reports_choice_bias():
    body = client.post("/api/attainment", json={
        "course_code": "KCS501", "course_name": "DBMS",
        "co_po_levels": {"CO1": {"PO1": 3}},
        "questions": [{
            "question_no": "Q1", "co_code": "CO1", "max_marks": 10,
            "student_marks": _marks(4, 9),
            "not_attempted": [f"21CS{i:03d}" for i in range(5, 11)],
            "choice_group": "G1",
        }],
    }).json()
    bias = body["choice_bias"]
    assert len(bias) == 1
    assert bias[0]["attempted_only_pct"] == 100.0
    assert bias[0]["cohort_pct"] == 40.0
    assert bias[0]["severity"] == "error"
    assert bias[0]["all_evidence_optional"] is True


def test_cohort_policy_changes_the_number():
    payload = {
        "course_code": "KCS501", "course_name": "DBMS",
        "co_po_levels": {"CO1": {"PO1": 3}},
        "questions": [{
            "question_no": "Q1", "co_code": "CO1", "max_marks": 10,
            "student_marks": _marks(4, 9),
            "not_attempted": [f"21CS{i:03d}" for i in range(5, 11)],
        }],
    }
    lenient = client.post("/api/attainment", json=payload).json()
    strict = client.post("/api/attainment",
                         json=payload | {"attempt_policy": "cohort"}).json()
    assert lenient["co_attainment"][0]["cie_success_pct"] == 100.0
    assert strict["co_attainment"][0]["cie_success_pct"] == 40.0
    assert strict["attempt_policy"] == "cohort"


def test_compulsory_paper_reports_no_choice_bias():
    body = client.post("/api/attainment", json={
        "course_code": "KCS501", "course_name": "DBMS",
        "co_po_levels": {"CO1": {"PO1": 3}},
        "questions": [{"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
                       "student_marks": _marks(10, 8)}],
    }).json()
    assert body["choice_bias"] == []


def test_pso_columns_appear_in_the_sar_tables():
    body = client.post("/api/attainment", json={
        "course_code": "KCS501", "course_name": "DBMS",
        "co_po_levels": {"CO1": {"PO1": 3}},
        "pso_codes": ["PSO1", "PSO2"],
        "co_pso_levels": {"CO1": {"PSO1": 3}},
        "questions": [{"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
                       "student_marks": _marks(10, 9)}],
    }).json()
    cam = body["tables"]["course_articulation_matrix"]
    assert cam["headers"][-2:] == ["PSO1", "PSO2"]
    assert cam["rows"][0][-2:] == ["3", "-"], "An unmapped PSO is '-', never 0."
    assert body["course_pso_attainment"]["PSO1"] is not None
    assert body["course_pso_attainment"]["PSO2"] is None
    assert body["tables"]["direct_attainment"]["headers"][-2:] == ["PSO1", "PSO2"]


def test_attainment_without_psos_is_unchanged():
    body = client.post("/api/attainment", json={
        "course_code": "KCS501", "course_name": "DBMS",
        "co_po_levels": {"CO1": {"PO1": 3}},
        "questions": [{"question_no": "Q1", "co_code": "CO1", "max_marks": 10,
                       "student_marks": _marks(10, 9)}],
    }).json()
    assert body["course_pso_attainment"] == {}
    assert "PSO1" not in body["tables"]["course_articulation_matrix"]["headers"]
