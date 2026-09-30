"""
Accounts, assignment and the Submit rule.

**These tests never touch the department's database.** `neon.database_url()`
finds `.env.local` by walking up from the module, so a test that simply called
the store would connect to production and the first `DELETE` would take the
real sheet with it. Everything below is therefore one of two kinds:

  * pure functions -- password hashing, the environment lookup, the 503 a
    route gives when nothing is linked -- which need no database at all;
  * integration tests, which run only when `AUTOOBE_TEST_DATABASE_URL` names
    a database *other than* the live one, and are skipped otherwise.

Create that database as a Neon branch when you want to run them:

    neon branches create --name test
    AUTOOBE_TEST_DATABASE_URL=$(neon connection-string test) python -m pytest tests/test_accounts.py
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.store import neon

TEST_URL = os.environ.get("AUTOOBE_TEST_DATABASE_URL", "").strip()


@pytest.fixture()
def client():
    return TestClient(app)


# ------------------------------------------------------- no database needed

def test_a_password_round_trips_and_a_wrong_one_does_not():
    stored = neon.hash_password("a-real-password")
    assert stored.startswith("scrypt$")
    assert neon.verify_password("a-real-password", stored)
    assert not neon.verify_password("a-real-passwore", stored)
    assert not neon.verify_password("", stored)


def test_two_hashes_of_one_password_differ():
    """Salted, so a stolen table does not reveal that two people share one
    password."""
    a = neon.hash_password("the-same-password")
    b = neon.hash_password("the-same-password")
    assert a != b
    assert neon.verify_password("the-same-password", a)
    assert neon.verify_password("the-same-password", b)


def test_a_short_password_is_refused():
    with pytest.raises(ValueError, match="8 characters"):
        neon.hash_password("short")


@pytest.mark.parametrize("junk", ["", "not-a-hash", "scrypt$bad", "md5$1$2$3$4$5"])
def test_a_damaged_hash_never_authenticates(junk):
    assert not neon.verify_password("anything", junk)


def test_the_engine_still_works_with_no_database(client, monkeypatch):
    """
    The whole point of keeping this layer optional. A laptop with no account
    and no network still maps a syllabus.
    """
    monkeypatch.setattr(neon, "database_url", lambda: "")
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3", "co_blooms": {"CO1": [4]},
        "competency": {"CO1": {"PO2": 2}}}).json()
    assert body["levels"]["CO1"]["PO2"] == 3


def test_an_unlinked_install_says_so_instead_of_crashing(client, monkeypatch):
    monkeypatch.setattr(neon, "database_url", lambda: "")
    monkeypatch.setattr(neon, "is_configured", lambda: False)
    body = client.get("/api/auth/status").json()
    assert body["configured"] is False
    assert "neon link" in body["detail"]
    # and a guarded route refuses rather than raising
    assert client.get("/api/my/courses").status_code in (401, 503)


def test_signed_out_visitors_are_refused_everywhere(client):
    """No cookie, no answer -- one route of every kind the pages call."""
    for method, path in (("get", "/api/auth/me"),
                         ("get", "/api/my/courses"),
                         ("get", "/api/admin/overview"),
                         ("get", "/api/admin/submissions"),
                         ("get", "/api/admin/workbook"),
                         ("get", "/api/admin/po-coverage"),
                         ("get", "/api/admin/catalogue-search?q=digital"),
                         ("post", "/api/admin/courses/bulk"),
                         ("post", "/api/me/password")):
        r = getattr(client, method)(path)
        assert r.status_code in (401, 422, 503), f"{path} answered {r.status_code}"


# --------------------------------------------------------- needs a database

pytestmark_db = pytest.mark.skipif(
    not TEST_URL,
    reason="set AUTOOBE_TEST_DATABASE_URL to a throwaway Neon branch to run these")


@pytest.fixture()
def db(monkeypatch):
    """A clean schema on the *test* database, never the live one."""
    live = neon.database_url()
    if TEST_URL and live and TEST_URL.strip() == live.strip():
        pytest.fail("AUTOOBE_TEST_DATABASE_URL is the live database. Refusing "
                    "to run destructive tests against it.")
    neon.reset_pool()
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    neon.ensure_schema()
    # Order matters: children before parents, or the foreign keys refuse.
    for table in ("attainment", "submissions", "courses", "sessions", "users",
                  "audit"):
        neon._exec(f"DELETE FROM {table}")
    yield neon
    neon.reset_pool()


@pytestmark_db
def test_a_teacher_sees_only_their_own_subject(db, client):
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    mine = db.create_user(username="one", password="teacher-password", role=db.TEACHER)
    yours = db.create_user(username="two", password="teacher-password", role=db.TEACHER)
    db.create_course(code="A-1", title="Mine", teacher_id=mine["id"])
    db.create_course(code="B-2", title="Yours", teacher_id=yours["id"])

    assert [c["code"] for c in db.courses_for_teacher(mine["id"])] == ["A-1"]
    assert [c["code"] for c in db.courses_for_teacher(yours["id"])] == ["B-2"]
    # The admin's view is the union, and it is a different query.
    assert {c["code"] for c in db.list_courses()} == {"A-1", "B-2"}

    # Ownership is asked of the database, not inferred by the caller.
    a1 = next(c for c in db.list_courses() if c["code"] == "A-1")
    assert db.teacher_owns(a1["id"], mine["id"])
    assert not db.teacher_owns(a1["id"], yours["id"])


@pytestmark_db
def test_nothing_is_stored_until_submit(db):
    teacher = db.create_user(username="teach1", password="teacher-password",
                             role=db.TEACHER)
    course = db.create_course(code="A-1", title="Mine", teacher_id=teacher["id"])
    # The teacher has done work; the database knows nothing about it.
    assert db.get_submission(course["id"]) is None
    assert db.all_submissions() == []
    assert db.stats()["submitted"] == 0

    db.submit_mapping(course_id=course["id"], user_id=teacher["id"],
                      framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO1": 3}}})
    assert db.get_submission(course["id"]) is not None
    assert db.stats()["submitted"] == 1


@pytestmark_db
def test_resubmitting_keeps_who_first_submitted_it(db):
    teacher = db.create_user(username="teach1", password="teacher-password",
                             role=db.TEACHER)
    admin = db.create_user(username="admin1", password="admin-password", role=db.ADMIN)
    course = db.create_course(code="A-1", title="Mine", teacher_id=teacher["id"])
    payload = {"cos": [{"code": "CO1"}], "co_levels": {"CO1": {"PO1": 3}}}
    db.submit_mapping(course_id=course["id"], user_id=teacher["id"],
                      framework_code="GAPC_V3", payload=payload)
    db.submit_mapping(course_id=course["id"], user_id=admin["id"],
                      framework_code="GAPC_V3",
                      payload={**payload, "co_levels": {"CO1": {"PO1": 1}}})
    row = db.get_submission(course["id"])
    assert row["submitted_by"] == "teach1" and row["updated_by"] == "admin1"
    assert row["payload"]["co_levels"]["CO1"]["PO1"] == 1


@pytestmark_db
def test_deleting_a_course_takes_its_mapping_with_it(db):
    teacher = db.create_user(username="teach1", password="teacher-password",
                             role=db.TEACHER)
    course = db.create_course(code="A-1", title="Mine", teacher_id=teacher["id"])
    db.submit_mapping(course_id=course["id"], user_id=teacher["id"],
                      framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO1": 3}}})
    db.delete_course(course["id"])
    assert db.get_submission(course["id"]) is None


@pytestmark_db
def test_changing_a_password_ends_every_session_on_that_account(db):
    user = db.create_user(username="teach1", password="teacher-password",
                          role=db.TEACHER)
    token, _ = db.login("teach1", "teacher-password")
    assert db.user_for_token(token) is not None
    db.set_password(user["id"], "a-new-password")
    assert db.user_for_token(token) is None
    assert db.login("teach1", "teacher-password") is None
    assert db.login("teach1", "a-new-password") is not None


@pytestmark_db
def test_a_teacher_cannot_reach_any_admin_route(db, client):
    """The role guard, on every route added for the dashboard."""
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    db.create_user(username="teach1", password="teacher-password", role=db.TEACHER)
    client.post("/api/auth/login",
                json={"username": "teach1", "password": "teacher-password"})
    for method, path, body in (
            ("get", "/api/admin/overview", None),
            ("get", "/api/admin/po-coverage", None),
            ("get", "/api/admin/catalogue-search?q=digital", None),
            ("get", "/api/admin/workbook", None),
            ("post", "/api/admin/courses/bulk", {"courses": []}),
            ("post", "/api/admin/users", {"username": "x2", "password": "12345678"})):
        r = (client.post(path, json=body) if method == "post" else client.get(path))
        assert r.status_code == 403, f"{path} answered {r.status_code}"


@pytestmark_db
def test_adding_many_subjects_reports_the_ones_that_did_not_take(db, client):
    """
    Forty-odd subjects arrive as one paste. A duplicate in the middle must not
    throw away the thirty rows either side of it.
    """
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    client.post("/api/auth/login",
                json={"username": "hod", "password": "admin-password"})
    db.create_course(code="BEC-301", title="Electronic Devices", academic_year="2024-25")

    body = client.post("/api/admin/courses/bulk", json={"courses": [
        {"code": "BEC-301", "title": "Electronic Devices", "academic_year": "2024-25"},
        {"code": "BEC-302", "title": "Digital System Design", "academic_year": "2024-25"},
        {"code": "BEC-303", "title": "Network Analysis", "academic_year": "2024-25"},
    ]}).json()
    assert [c["code"] for c in body["added"]] == ["BEC-302", "BEC-303"]
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["code"] == "BEC-301"
    assert "already exists" in body["skipped"][0]["reason"]


@pytestmark_db
def test_the_catalogue_finds_a_subject_so_it_is_picked_not_typed(db, client):
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    client.post("/api/auth/login",
                json={"username": "hod", "password": "admin-password"})
    hits = client.get("/api/admin/catalogue-search",
                      params={"q": "antenna wave propagation"}).json()["results"]
    assert hits, "the bundled AKTU catalogue found nothing"
    assert any("ANTENNA" in h["title"].upper() for h in hits)
    # and it comes back with what the sheet needs, not just a name
    assert all(h["code"] and h["title"] for h in hits)
    assert client.get("/api/admin/catalogue-search").json()["results"] == []


@pytestmark_db
def test_programme_coverage_names_the_untouched_pos(db, client):
    """
    A course block answers "does this subject serve PO4?". Only the union of
    every submitted block answers "does this programme serve PO4?", and the
    POs nothing reaches are the finding.
    """
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    teacher = db.create_user(username="teach1", password="teacher-password",
                             role=db.TEACHER)
    a = db.create_course(code="A-1", title="One", teacher_id=teacher["id"])
    b = db.create_course(code="B-2", title="Two", teacher_id=teacher["id"])
    db.submit_mapping(course_id=a["id"], user_id=teacher["id"],
                      framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO1": 3, "PO2": 1}}})
    db.submit_mapping(course_id=b["id"], user_id=teacher["id"],
                      framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO1": 1}}})

    client.post("/api/auth/login",
                json={"username": "hod", "password": "admin-password"})
    cov = client.get("/api/admin/po-coverage").json()
    per = {p["po_code"]: p for p in cov["pos"]}
    assert cov["courses"] == 2
    # PO1 is 3 in one subject and 1 in the other -> 2.00 over two subjects.
    assert per["PO1"]["average"] == 2.0 and per["PO1"]["courses"] == 2
    # PO2 is reached by one subject only, and is not dragged down by the other.
    assert per["PO2"]["average"] == 1.0 and per["PO2"]["courses"] == 1
    assert per["PO7"]["average"] is None and per["PO7"]["courses"] == 0
    assert "PO7" in cov["untouched"] and "PO1" not in cov["untouched"]


@pytestmark_db
def test_changing_your_own_password_needs_the_current_one(db, client):
    db.create_user(username="teach1", password="teacher-password", role=db.TEACHER)
    client.post("/api/auth/login",
                json={"username": "teach1", "password": "teacher-password"})

    r = client.post("/api/me/password",
                    json={"current": "wrong-one", "new": "a-new-password"})
    assert r.status_code == 403
    r = client.post("/api/me/password",
                    json={"current": "teacher-password", "new": "short"})
    assert r.status_code == 400

    r = client.post("/api/me/password",
                    json={"current": "teacher-password", "new": "a-new-password"})
    assert r.status_code == 200
    # The session that did it survives; the old password does not.
    assert client.get("/api/auth/me").status_code == 200
    assert db.login("teach1", "teacher-password") is None


# --------------------------------------------- a sleeping database is not a
#                                                broken one

def test_an_unlinked_install_and_a_sleeping_one_are_told_apart(client, monkeypatch):
    """
    Neon scales its compute to zero, so the first request after an idle spell
    can take several seconds. An earlier version reported that as
    `configured: false`, and the sign-in page drew it as a dead end reading
    "The database is not connected" — on an install that was perfectly
    healthy and merely asleep.

    Three states, because a person can only act on one of them.
    """
    monkeypatch.setattr(neon, "is_configured", lambda: False)
    body = client.get("/api/auth/status").json()
    assert body["state"] == "unlinked" and body["configured"] is False
    assert "neon link" in body["detail"]

    def asleep():
        raise TimeoutError("connection timeout expired")

    monkeypatch.setattr(neon, "is_configured", lambda: True)
    monkeypatch.setattr(neon, "ensure_schema", asleep)
    body = client.get("/api/auth/status").json()
    assert body["state"] == "waking"
    # Configured stays true: the install is fine, this one request was not,
    # and the page retries rather than giving up.
    assert body["configured"] is True
    assert "did not answer" in body["detail"]


def test_health_reports_the_engine_and_the_database_separately(client, monkeypatch):
    """The mapping engine is up whether or not the database is. A monitor
    that conflates them pages somebody at three in the morning for a
    Postgres that is merely asleep."""
    monkeypatch.setattr(neon, "is_configured", lambda: False)
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["database"] == "unlinked"

    def asleep():
        raise TimeoutError("nope")

    monkeypatch.setattr(neon, "is_configured", lambda: True)
    monkeypatch.setattr(neon, "ensure_schema", asleep)
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["database"] == "waking"


def test_a_username_signs_in_however_it_is_capitalised(monkeypatch):
    """Accounts are created and matched in lower case, so the name on the
    card and the name typed at the keyboard do not have to agree."""
    seen = {}

    def fake_one(sql, params=()):
        seen["params"] = params
        return None            # no such user; login declines, having lowered it

    monkeypatch.setattr(neon, "_one", fake_one)
    assert neon.login("Madhur", "whatever") is None
    assert seen["params"] == ("madhur",)
    neon.login("  MADHUR  ", "whatever")
    assert seen["params"] == ("madhur",)


# ------------------------------------------ PO attainment and the CQI loop

def _two_halves(db, client):
    """Two courses with both a mapping and an attainment, small enough to
    check on paper. See the numbers in the tests below."""
    admin = db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    t = db.create_user(username="teach1", password="teacher-password", role=db.TEACHER)
    a = db.create_course(code="AA-1", title="A", academic_year="2023-24",
                         teacher_id=t["id"])
    b = db.create_course(code="BB-2", title="B", academic_year="2023-24",
                         teacher_id=t["id"])
    # A third with a mapping and no marks: it must not reach the average.
    c = db.create_course(code="CC-3", title="C", academic_year="2023-24",
                         teacher_id=t["id"])

    db.submit_mapping(course_id=a["id"], user_id=t["id"], framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}, {"code": "CO2"}],
                               "co_levels": {"CO1": {"PO1": 3, "PO2": 2},
                                             "CO2": {"PO1": 2}}})
    db.submit_mapping(course_id=b["id"], user_id=t["id"], framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO1": 1}}})
    db.submit_mapping(course_id=c["id"], user_id=t["id"], framework_code="GAPC_V3",
                      payload={"cos": [{"code": "CO1"}],
                               "co_levels": {"CO1": {"PO5": 3}}})

    db.submit_attainment(
        course_id=a["id"], user_id=t["id"], t1_pct=45, t2_pct=60,
        internal=2.25, students=10,
        payload={"cos": [{"co_code": "CO1", "attainment": 3.0},
                         {"co_code": "CO2", "attainment": 1.5}]})
    db.submit_attainment(
        course_id=b["id"], user_id=t["id"], t1_pct=45, t2_pct=60,
        internal=1.0, students=10,
        payload={"cos": [{"co_code": "CO1", "attainment": 1.0}]})

    client.post("/api/auth/login",
                json={"username": "hod", "password": "admin-password"})
    return admin, t


@pytestmark_db
def test_po_attainment_is_the_mapping_times_the_marks(db, client):
    """
    Course A: CO1 → PO1 weight 3, PO2 weight 2, attained 3.0
              CO2 → PO1 weight 2,               attained 1.5
        PO1 = (3.0×3 + 1.5×2) / (3+2) = 12/5 = 2.40
        PO2 = (3.0×2) / 2                     = 3.00
    Course B: PO1 = 1.00
    Programme PO1 = mean(2.40, 1.00) = 1.70
    """
    _two_halves(db, client)
    d = client.get("/api/admin/po-attainment", params={"target": 2.0}).json()
    per = {c["code"]: c["po"] for c in d["courses"]}
    assert per["AA-1"]["PO1"] == pytest.approx(2.40, abs=0.005)
    assert per["AA-1"]["PO2"] == pytest.approx(3.00, abs=0.005)
    assert d["direct_attainment"]["PO1"] == pytest.approx(1.70, abs=0.005)
    assert d["direct_attainment"]["PO2"] == pytest.approx(3.00, abs=0.005)


@pytestmark_db
def test_a_po_no_course_addresses_is_null_rather_than_zero(db, client):
    """
    "Not taught here" and "taught and not attained" are different findings and
    a peer team reads them differently. Zero would say the second.
    """
    _two_halves(db, client)
    d = client.get("/api/admin/po-attainment").json()
    assert d["direct_attainment"]["PO4"] is None
    assert "PO4" in d["untouched"]
    # ...and an untouched PO is a curriculum gap, never a CQI action.
    assert "PO4" not in {r["code"] for r in d["cqi"]}


@pytestmark_db
def test_only_courses_with_both_halves_are_counted(db, client):
    """
    A course with a mapping and no marks would drag the average toward
    whichever half it has. It is excluded, and reported instead.
    """
    _two_halves(db, client)
    d = client.get("/api/admin/po-attainment").json()
    assert [c["code"] for c in d["courses"]] == ["AA-1", "BB-2"]
    r = d["readiness"]
    assert r["courses"] == 3 and r["both"] == 2
    assert r["mapping_only"] == 1 and r["marks_only"] == 0
    # CC-3 maps PO5; because it has no marks, PO5 stays untouched.
    assert d["direct_attainment"]["PO5"] is None


@pytestmark_db
def test_the_loop_drafts_an_action_for_every_po_under_target(db, client):
    _two_halves(db, client)
    d = client.get("/api/admin/po-attainment", params={"target": 2.0}).json()
    cqi = {r["code"]: r for r in d["cqi"]}
    assert set(cqi) == {"PO1"}, "only PO1 is under 2.0"
    assert cqi["PO1"]["gap"] == pytest.approx(0.30, abs=0.005)
    assert cqi["PO1"]["action"] and cqi["PO1"]["rationale"]

    # The target is the department's decision, and it moves the severity.
    harder = client.get("/api/admin/po-attainment", params={"target": 2.8}).json()
    hard = {r["code"]: r for r in harder["cqi"]}
    assert hard["PO1"]["gap"] == pytest.approx(1.10, abs=0.005)
    assert hard["PO1"]["severity"] != cqi["PO1"]["severity"]
    # PO2 sits at 3.00 and so stays out even of the harder target -- raising
    # the bar must not manufacture an action for a PO that already clears it.
    assert "PO2" not in hard


@pytestmark_db
def test_the_sar_tables_download_and_only_for_the_admin(db, client):
    _two_halves(db, client)
    r = client.get("/api/admin/sar", params={"target": 2.0})
    assert r.status_code == 200
    assert r.content[:2] == b"PK" and len(r.content) > 4000
    assert "PO_Attainment_CQI" in r.headers.get("content-disposition", "")

    teacher = TestClient(app)
    teacher.post("/api/auth/login",
                 json={"username": "teach1", "password": "teacher-password"})
    assert teacher.get("/api/admin/sar").status_code == 403
    assert teacher.get("/api/admin/po-attainment").status_code == 403


@pytestmark_db
def test_nothing_to_multiply_says_so_rather_than_exporting_an_empty_table(db, client):
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)
    client.post("/api/auth/login",
                json={"username": "hod", "password": "admin-password"})
    d = client.get("/api/admin/po-attainment").json()
    assert d["courses"] == [] and d["cqi"] == []
    assert all(v is None for v in d["direct_attainment"].values())
    r = client.get("/api/admin/sar")
    assert r.status_code == 400 and "no PO attainment" in r.json()["detail"]


# --------------------------------------- the older routes are guarded too

def test_the_workspace_routes_are_open_on_an_offline_copy(client):
    """
    No DATABASE_URL means a single-user install with nobody to sign in as.
    Refusing there would break the offline product to protect nothing — and
    the whole offline suite runs through this path.
    """
    assert not neon.is_configured(), "conftest should have cleared DATABASE_URL"
    assert client.get("/api/programs").status_code == 200
    assert client.get("/api/audit").status_code in (200, 404)


@pytestmark_db
def test_the_workspace_routes_need_a_session_once_accounts_exist(db, client):
    """
    The hole this closes: these routes took the actor and the role from
    request headers and believed them, so anyone who could reach the API could
    read the audit trail by claiming to be the coordinator.
    """
    db.create_user(username="hod", password="admin-password", role=db.ADMIN)

    anon = TestClient(app)
    claimed = {"X-Actor": "anyone@anywhere", "X-Role": "coordinator"}
    for path in ("/api/programs", "/api/audit"):
        assert anon.get(path, headers=claimed).status_code == 401, path

    # A teacher is signed in, so they get through — the session decides
    # *whether*, the header still decides *who*, and the approval chain is
    # unchanged.
    db.create_user(username="teach1", password="teacher-password", role=db.TEACHER)
    signed = TestClient(app)
    signed.post("/api/auth/login",
                json={"username": "teach1", "password": "teacher-password"})
    assert signed.get("/api/programs").status_code == 200


@pytestmark_db
def test_an_install_with_a_database_but_no_admin_is_still_open(db, client):
    """So a fresh install can be set up before it is locked."""
    assert db.count_admins() == 0
    assert client.get("/api/programs").status_code == 200
