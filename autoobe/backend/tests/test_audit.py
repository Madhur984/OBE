"""The audit chain and the evidence bundle."""
from __future__ import annotations

import json

import pytest

from app.store import workflow
from app.store.audit import (
    GENESIS, build_evidence_bundle, events, record, verify_chain,
)
from app.store.db import Database


@pytest.fixture
def store():
    db = Database(":memory:")
    db.create_tenant("College", tenant_id="t")
    yield db.tenant("t")
    db.close()


@pytest.fixture
def course(store):
    pid = store.create_program("CSE", "B.Tech CSE", "GAPC_V4", branch="CSE")
    cid = store.create_course(pid, "KCS501", "Database Management Systems",
                              semester=5, credits=4, ltp="3-1-0")
    store.set_cos(cid, [
        ("CO1", "Explain the architecture of a database management system.", [2], None),
        ("CO2", "Apply entity-relationship modelling to design a schema.", [3],
         "Student should be aware of ER modelling"),
    ])
    return cid


def test_first_event_chains_from_genesis(store):
    e = record(store, actor="fac@x.edu", action="co.created",
               entity_type="course", entity_id="c1")
    assert e.seq == 1
    assert e.prev_hash == GENESIS
    assert len(e.hash) == 64


def test_events_chain_in_sequence(store):
    a = record(store, actor="x", action="one", entity_type="course", entity_id="c1")
    b = record(store, actor="x", action="two", entity_type="course", entity_id="c1")
    c = record(store, actor="x", action="three", entity_type="course", entity_id="c1")
    assert (a.seq, b.seq, c.seq) == (1, 2, 3)
    assert b.prev_hash == a.hash
    assert c.prev_hash == b.hash
    assert verify_chain(store).ok


def test_identical_payloads_still_produce_distinct_hashes(store):
    a = record(store, actor="x", action="same", entity_type="course",
               entity_id="c1", payload={"level": 3})
    b = record(store, actor="x", action="same", entity_type="course",
               entity_id="c1", payload={"level": 3})
    assert a.hash != b.hash, "The sequence number must enter the digest."


def test_payload_key_order_does_not_change_the_hash(store):
    """
    Two logically identical events must digest identically, or verification
    becomes flaky in a way that looks like tampering.
    """
    from app.store.audit import _digest
    one = json.dumps({"a": 1, "b": 2}, sort_keys=True, separators=(",", ":"))
    two = json.dumps({"b": 2, "a": 1}, sort_keys=True, separators=(",", ":"))
    args = dict(tenant_id="t", seq=1, at="2026-01-01T00:00:00+00:00", actor="x",
                action="y", entity_type="course", entity_id="c", prev_hash=GENESIS)
    assert _digest(payload=one, **args) == _digest(payload=two, **args)


def test_editing_an_event_is_detected(store):
    record(store, actor="fac", action="mapping.set", entity_type="course",
           entity_id="c1", payload={"po": "PO1", "level": 1})
    record(store, actor="fac", action="mapping.set", entity_type="course",
           entity_id="c1", payload={"po": "PO2", "level": 2})
    record(store, actor="fac", action="attainment.run", entity_type="course",
           entity_id="c1")

    # Someone quietly upgrades a mapping the week before the visit.
    store._write(
        "UPDATE audit_events SET payload = ? WHERE tenant_id = ? AND seq = 1",
        (json.dumps({"po": "PO1", "level": 3}), store.tenant_id),
    )

    check = verify_chain(store)
    assert not check.ok
    assert any("was altered after it was written" in p for p in check.problems)
    assert "Event 1" in check.problems[0]


def test_deleting_an_event_is_detected(store):
    for i in range(4):
        record(store, actor="x", action=f"a{i}", entity_type="course", entity_id="c1")
    store._write("DELETE FROM audit_events WHERE tenant_id = ? AND seq = 2",
                 (store.tenant_id,))

    check = verify_chain(store)
    assert not check.ok
    assert any("deleted" in p for p in check.problems)


def test_an_empty_log_verifies(store):
    check = verify_chain(store)
    assert check.ok and check.checked == 0


def test_events_are_filterable_by_entity(store):
    record(store, actor="x", action="a", entity_type="course", entity_id="c1")
    record(store, actor="x", action="b", entity_type="course", entity_id="c2")
    record(store, actor="x", action="c", entity_type="course", entity_id="c1")
    assert [e.action for e in events(store, entity_id="c1")] == ["a", "c"]


def test_audit_log_is_tenant_scoped():
    db = Database(":memory:")
    try:
        db.create_tenant("A", tenant_id="a")
        db.create_tenant("B", tenant_id="b")
        a, b = db.tenant("a"), db.tenant("b")
        record(a, actor="x", action="a1", entity_type="course", entity_id="c")
        record(b, actor="y", action="b1", entity_type="course", entity_id="c")
        # Each tenant's chain starts at 1 and contains only its own events.
        assert [e.action for e in events(a)] == ["a1"]
        assert [e.action for e in events(b)] == ["b1"]
        assert verify_chain(a).ok and verify_chain(b).ok
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Evidence bundles
# ---------------------------------------------------------------------------

def _populate(store, cid):
    store.set_mapping(cid, "CO1", "PO", "PO1", competency=2, level=3,
                      source="proposed", evidence="Matched content terms: architecture.")
    store.set_mapping(cid, "CO2", "PO", "PO3", competency=2, level=3,
                      source="override", evidence="Set by faculty.")
    aid = store.create_assessment(cid, kind="cie", name="Sessional 1",
                                  academic_year="2025-26")
    q1 = store.add_question(aid, question_no="Q1", co_code="CO1", max_marks=10)
    q2 = store.add_question(aid, question_no="Q2", co_code="CO2", max_marks=10)
    for roll, m1, m2 in [("21CS001", 8, 7), ("21CS002", 6, 9), ("21CS003", 9, 5)]:
        store.set_mark(q1, roll, m1, "present")
        store.set_mark(q2, roll, m2, "present")
    store.set_mark(q1, "21CS004", None, "absent")

    run_id = store.save_run(
        academic_year="2025-26", scope="course", course_id=cid,
        config={"t1": 60, "t2": 70, "t3": 80, "target_pct": 60,
                "mode": "fixed_threshold"},
        result={"co_attainment": {
            "CO1": {"success_pct": 66.7, "level": 1, "questions": [
                {"question_no": "Q1", "students_meeting_target": 2,
                 "passed_pct": 66.7, "target_marks": 6.0}]},
            "CO2": {"success_pct": 66.7, "level": 1, "questions": [
                {"question_no": "Q2", "students_meeting_target": 2,
                 "passed_pct": 66.7, "target_marks": 6.0}]},
        }},
        created_by="fac@x.edu",
    )
    record(store, actor="fac@x.edu", action="cos.imported", entity_type="course",
           entity_id=cid, payload={"count": 2})
    record(store, actor="fac@x.edu", action="mapping.override", entity_type="course",
           entity_id=cid, payload={"co": "CO2", "po": "PO3", "level": 3})
    return run_id


def test_bundle_carries_every_input_behind_the_numbers(store, course):
    _populate(store, course)
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")

    assert b.course["code"] == "KCS501"
    assert {c["code"] for c in b.outcomes} == {"CO1", "CO2"}
    assert b.thresholds["t3"] == 80
    assert b.result["co_attainment"]["CO1"]["success_pct"] == 66.7
    assert b.chain["ok"] is True
    assert len(b.audit) == 2


def test_bundle_preserves_override_provenance(store, course):
    _populate(store, course)
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    by_co = {m["co_code"]: m for m in b.mapping}
    assert by_co["CO1"]["source"] == "proposed"
    assert by_co["CO2"]["source"] == "override", \
        "A peer team asks which cells a human changed; that must survive."


def test_bundle_records_absentees_separately_from_marks(store, course):
    _populate(store, course)
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    q1 = next(q for a in b.assessments for q in a["questions"] if q["question_no"] == "Q1")
    assert q1["present"] == 3
    assert q1["absent"] == 1, "An absentee must never be folded into the denominator."


def test_bundle_merges_the_computed_target_into_each_question(store, course):
    _populate(store, course)
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    q1 = next(q for a in b.assessments for q in a["questions"] if q["question_no"] == "Q1")
    assert q1["target_marks"] == 6.0
    assert q1["met"] == 2


def test_bundle_includes_approval_history(store, course):
    _populate(store, course)
    workflow.transition(store, entity_type="course_matrix", entity_id=course,
                        action="submit", actor="fac@x.edu", role="faculty")
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    assert [a["state"] for a in b.approvals] == ["submitted"]


def test_bundle_finds_events_filed_under_the_course_code(store, course):
    """
    Approvals and audit events are keyed by the course code, because that is
    stable and exists before anything is stored. Looking them up by the row id
    returned an empty change log, which reads as "nothing happened".
    """
    _populate(store, course)
    record(store, actor="hod@x.edu", action="mapping.reviewed",
           entity_type="course_matrix", entity_id="KCS501")
    workflow.transition(store, entity_type="course_matrix", entity_id="KCS501",
                        action="submit", actor="fac@x.edu", role="faculty")

    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    assert any(e["action"] == "mapping.reviewed" for e in b.audit)
    assert [a["state"] for a in b.approvals] == ["submitted"]


def test_bundle_merges_events_from_both_keys_without_duplicates(store, course):
    _populate(store, course)                                   # files under course id
    record(store, actor="x", action="under.code", entity_type="course_matrix",
           entity_id="KCS501")
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    seqs = [e["seq"] for e in b.audit]
    assert seqs == sorted(set(seqs)), "Events must not appear twice or out of order."
    assert len(seqs) == 3


def test_bundle_reports_a_broken_chain_rather_than_hiding_it(store, course):
    _populate(store, course)
    store._write("DELETE FROM audit_events WHERE tenant_id = ? AND seq = 1",
                 (store.tenant_id,))
    b = build_evidence_bundle(store, course_id=course, academic_year="2025-26")
    assert b.chain["ok"] is False
    assert b.chain["problems"]


def test_bundle_renders_markdown(store, course):
    _populate(store, course)
    md = build_evidence_bundle(store, course_id=course,
                               academic_year="2025-26").to_markdown()
    assert "# Evidence bundle — KCS501" in md
    assert "| CO2 | PO3 | 3 | 2 | override |" in md
    assert "rewritten" in md, "The CO with an original_statement is a rewrite."


def test_bundle_is_stable_after_the_engine_changes(store, course):
    """
    The bundle reads stored results, never recomputed ones. A bundle from a
    past year must show what was submitted, not what today's code produces.
    """
    run_id = _populate(store, course)
    before = build_evidence_bundle(store, course_id=course,
                                   academic_year="2025-26", run_id=run_id)
    # Simulate the mapping being changed later.
    store.set_mapping(course, "CO1", "PO", "PO1", competency=1, level=1,
                      source="override")
    after = build_evidence_bundle(store, course_id=course,
                                  academic_year="2025-26", run_id=run_id)
    assert before.result == after.result, "Stored attainment must not drift."
