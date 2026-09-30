"""Persistence, tenant isolation, and the approval chain."""
from __future__ import annotations

import pytest

from app.store.db import CrossTenantAccess, Database, bootstrap
from app.store import workflow


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def two_colleges(db):
    a = db.create_tenant("College A", tenant_id="a")
    b = db.create_tenant("College B", tenant_id="b")
    return db.tenant(a.id), db.tenant(b.id)


def test_round_trip_survives_a_new_connection(tmp_path):
    path = tmp_path / "obe.sqlite3"
    db, store = bootstrap(path)
    pid = store.create_program("CSE", "B.Tech CSE", "GAPC_V4", branch="CSE")
    cid = store.create_course(pid, "KCS501", "Database Management Systems")
    store.set_cos(cid, [("CO1", "Explain the relational model.", [2], None)])
    db.close()

    db2, store2 = bootstrap(path)
    try:
        course = store2.course_by_code(store2.list_programs()[0]["id"], "KCS501")
        assert course["title"] == "Database Management Systems"
        cos = store2.list_cos(course["id"])
        assert cos[0]["blooms_levels"] == [2], "JSON columns must round-trip typed."
    finally:
        db2.close()


def test_tenants_cannot_see_each_others_courses(two_colleges):
    a, b = two_colleges
    pa = a.create_program("CSE", "B.Tech CSE", "GAPC_V4")
    a.create_course(pa, "KCS501", "DBMS")
    pb = b.create_program("CSE", "B.Tech CSE", "GAPC_V4")
    b.create_course(pb, "KCS501", "Someone else's DBMS")

    assert len(a.list_courses(pa)) == 1
    assert a.list_courses(pa)[0]["title"] == "DBMS"
    # Same course code in both tenants is legal and must not collide.
    assert b.list_courses(pb)[0]["title"] == "Someone else's DBMS"


def test_asking_for_another_tenants_row_by_id_returns_nothing(two_colleges):
    a, b = two_colleges
    pb = b.create_program("CSE", "B.Tech CSE", "GAPC_V4")
    cb = b.create_course(pb, "KCS501", "B's course")
    # A knows the id -- it still must not resolve.
    assert a.get_course(cb) is None
    assert a.get_program(pb) is None


def test_a_query_missing_its_tenant_predicate_raises(two_colleges):
    """
    The second net. If someone extends the store with a query and forgets the
    WHERE clause, the row-level guard must catch it rather than leaking.
    """
    a, b = two_colleges
    pb = b.create_program("CSE", "B.Tech CSE", "GAPC_V4")
    b.create_course(pb, "KCS501", "B's course")

    with pytest.raises(CrossTenantAccess) as exc:
        a._rows("SELECT * FROM courses")           # deliberately unscoped
    assert "missing a tenant predicate" in str(exc.value)


def test_tenant_store_exposes_no_tenant_id_parameter():
    """
    Isolation is structural: if no method accepts a tenant id, no call site
    can address the wrong college. This test fails the moment that stops
    being true.
    """
    import inspect
    from app.store.db import TenantStore

    offenders = []
    for name, member in inspect.getmembers(TenantStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        params = inspect.signature(member).parameters
        if "tenant_id" in params:
            offenders.append(name)
    assert not offenders, f"These methods take a tenant_id: {offenders}"


def test_unknown_tenant_is_rejected(db):
    with pytest.raises(KeyError):
        db.tenant("does-not-exist")


def test_mapping_upsert_replaces_rather_than_duplicates(db):
    store = db.tenant(db.create_tenant("T", tenant_id="t").id)
    pid = store.create_program("CSE", "B.Tech", "GAPC_V4")
    cid = store.create_course(pid, "KCS501", "DBMS")

    store.set_mapping(cid, "CO1", "PO", "PO1", competency="partial", level=1,
                      source="proposed")
    store.set_mapping(cid, "CO1", "PO", "PO1", competency="strong", level=3,
                      source="override")

    rows = store.list_mapping(cid)
    assert len(rows) == 1
    assert rows[0]["level"] == 3
    assert rows[0]["source"] == "override"


def test_mapping_rejects_unknown_kinds(db):
    store = db.tenant(db.create_tenant("T", tenant_id="t").id)
    pid = store.create_program("CSE", "B.Tech", "GAPC_V4")
    cid = store.create_course(pid, "KCS501", "DBMS")
    with pytest.raises(ValueError):
        store.set_mapping(cid, "CO1", "PEO", "PEO1", competency="strong",
                          level=3, source="proposed")
    with pytest.raises(ValueError):
        store.set_mapping(cid, "CO1", "PO", "PO1", competency="strong",
                          level=3, source="guessed")


def test_psos_are_replaced_wholesale(db):
    store = db.tenant(db.create_tenant("T", tenant_id="t").id)
    pid = store.create_program("CSE", "B.Tech", "GAPC_V4")
    store.set_psos(pid, [("PSO1", "First"), ("PSO2", "Second")])
    store.set_psos(pid, [("PSO1", "Rewritten")])
    psos = store.list_psos(pid)
    assert [p["code"] for p in psos] == ["PSO1"]
    assert psos[0]["statement"] == "Rewritten"


def test_marks_distinguish_absent_from_not_attempted(db):
    store = db.tenant(db.create_tenant("T", tenant_id="t").id)
    pid = store.create_program("CSE", "B.Tech", "GAPC_V4")
    cid = store.create_course(pid, "KCS501", "DBMS")
    aid = store.create_assessment(cid, kind="cie", name="Sessional 1",
                                  academic_year="2025-26")
    qid = store.add_question(aid, question_no="Q1", co_code="CO1", max_marks=10)
    store.set_mark(qid, "21CS001", 8, "present")
    store.set_mark(qid, "21CS002", None, "absent")
    store.set_mark(qid, "21CS003", None, "not_attempted")

    statuses = {m["roll"]: m["status"] for m in store.list_marks(qid)}
    assert statuses["21CS002"] == "absent"
    assert statuses["21CS003"] == "not_attempted"

    with pytest.raises(ValueError):
        store.set_mark(qid, "21CS004", None, "maybe")


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------

@pytest.fixture
def course(db):
    store = db.tenant(db.create_tenant("T", tenant_id="t").id)
    pid = store.create_program("CSE", "B.Tech", "GAPC_V4")
    return store, store.create_course(pid, "KCS501", "DBMS")


def test_new_entities_start_as_draft(course):
    store, cid = course
    assert workflow.current_state(store, "course_matrix", cid).state == "draft"


def test_happy_path_to_locked(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="fac@x.edu", role="faculty")
    workflow.transition(store, **kw, action="approve", actor="hod@x.edu", role="hod")
    st = workflow.transition(store, **kw, action="lock", actor="nba@x.edu",
                             role="coordinator")
    assert st.state == "locked"
    assert st.is_locked and not st.is_editable


def test_faculty_cannot_approve_their_own_work(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="fac@x.edu", role="faculty")
    with pytest.raises(workflow.TransitionDenied) as exc:
        workflow.transition(store, **kw, action="approve", actor="fac@x.edu",
                            role="faculty")
    assert "cannot 'approve'" in str(exc.value)


def test_hod_cannot_lock(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    workflow.transition(store, **kw, action="approve", actor="h", role="hod")
    with pytest.raises(workflow.TransitionDenied):
        workflow.transition(store, **kw, action="lock", actor="h", role="hod")


def test_locked_is_terminal_and_says_why(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    workflow.transition(store, **kw, action="approve", actor="h", role="hod")
    workflow.transition(store, **kw, action="lock", actor="n", role="coordinator")

    with pytest.raises(workflow.TransitionDenied) as exc:
        workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    assert "locked" in str(exc.value).lower()
    with pytest.raises(workflow.TransitionDenied):
        workflow.require_editable(store, "course_matrix", cid)


def test_unlock_requires_a_coordinator_and_a_reason(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    for action, role in (("submit", "faculty"), ("approve", "hod"), ("lock", "coordinator")):
        workflow.transition(store, **kw, action=action, actor="x", role=role)

    with pytest.raises(workflow.TransitionDenied):
        workflow.unlock(store, **kw, actor="h", role="hod", reason="please")
    with pytest.raises(workflow.TransitionDenied) as exc:
        workflow.unlock(store, **kw, actor="n", role="coordinator", reason="  ")
    assert "reason" in str(exc.value)

    st = workflow.unlock(store, **kw, actor="n", role="coordinator",
                         reason="Q3 marks were transcribed from the wrong section.")
    assert st.state == "returned"
    assert any("UNLOCKED" in h["note"] for h in
               workflow.history(store, "course_matrix", cid))


def test_returned_work_can_be_resubmitted(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    workflow.transition(store, **kw, action="return", actor="h", role="hod",
                        note="CO4 has no question mapped to it.")
    assert workflow.current_state(store, **kw).state == "returned"
    st = workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    assert st.state == "submitted"


def test_available_actions_depend_on_role():
    assert workflow.available_actions("submitted", "faculty") == []
    assert workflow.available_actions("submitted", "hod") == ["approve", "return"]
    assert workflow.available_actions("approved", "coordinator") == ["lock", "return"]
    assert workflow.available_actions("locked", "admin") == []


def test_history_is_ordered_and_complete(course):
    store, cid = course
    kw = dict(entity_type="course_matrix", entity_id=cid)
    workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    workflow.transition(store, **kw, action="return", actor="h", role="hod")
    workflow.transition(store, **kw, action="submit", actor="f", role="faculty")
    states = [h["state"] for h in workflow.history(store, "course_matrix", cid)]
    assert states == ["submitted", "returned", "submitted"]
