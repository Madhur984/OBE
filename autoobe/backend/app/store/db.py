"""
Persistence with structural tenant isolation.

Two decisions worth stating up front, because both are deliberate:

**SQLite by default, Postgres for deployment.** The plan specifies Postgres
with row-level security. That is right for a multi-college deployment, but
demanding a running database server to open the app would destroy the
zero-setup property the rest of the codebase has. So the store is written
against a narrow SQL surface, SQLite is the default and the one the test
suite exercises, and `postgres_schema.sql` carries the real `CREATE POLICY`
statements for the deployed path. What is *not* claimed anywhere: that the
Postgres path has been run against a live server here.

**Isolation is structural, not a WHERE clause faculty must remember.**
`Database.tenant(id)` returns a `TenantStore` bound to one tenant. No method
on it accepts a tenant id, so there is no call site that *can* ask for
another college's data. As a second net, every row that comes back carrying
a `tenant_id` column is checked against the binding and raises
`CrossTenantAccess` on mismatch -- that catches the realistic failure, which
is not malice but a query someone extended and forgot to scope.

Student marks are personal data under the DPDP Act 2023. The consequence
here is that tenant leakage is a reportable breach, not a bug, which is why
it is enforced by construction and asserted on read.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    email       TEXT NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL,          -- faculty | hod | coordinator | admin
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS programs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT '',
    framework_code  TEXT NOT NULL,
    UNIQUE (tenant_id, code)
);

-- Program Specific Outcomes are authored per department, so unlike POs they
-- cannot be seeded from a national framework. They are stored, versioned by
-- program, and inferred against exactly like POs once written.
CREATE TABLE IF NOT EXISTS psos (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    program_id  TEXT NOT NULL REFERENCES programs(id),
    code        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, program_id, code)
);

CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    program_id  TEXT NOT NULL REFERENCES programs(id),
    code        TEXT NOT NULL,
    title       TEXT NOT NULL,
    semester    INTEGER NOT NULL DEFAULT 0,
    credits     REAL NOT NULL DEFAULT 0,
    ltp         TEXT NOT NULL DEFAULT '0-0-0',
    scheme      TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, program_id, code)
);

CREATE TABLE IF NOT EXISTS cos (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id),
    course_id           TEXT NOT NULL REFERENCES courses(id),
    code                TEXT NOT NULL,
    statement           TEXT NOT NULL,
    blooms_levels       TEXT NOT NULL DEFAULT '[]',   -- JSON int array
    original_statement  TEXT,                          -- pre-rewrite provenance
    position            INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, course_id, code)
);

-- One row per CO x outcome-column. `target_kind` distinguishes PO from PSO
-- so both articulation matrices come out of one table.
CREATE TABLE IF NOT EXISTS co_outcome_map (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    course_id       TEXT NOT NULL REFERENCES courses(id),
    co_code         TEXT NOT NULL,
    target_kind     TEXT NOT NULL,       -- 'PO' | 'PSO'
    target_code     TEXT NOT NULL,
    framework_code  TEXT NOT NULL DEFAULT '',
    competency      INTEGER NOT NULL,    -- 0 no match | 1 partial | 2 strong
    level           INTEGER NOT NULL,    -- 0..3 (0 renders as '-')
    source          TEXT NOT NULL,       -- proposed | override
    evidence        TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, course_id, co_code, target_kind, target_code)
);

CREATE TABLE IF NOT EXISTS assessments (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    course_id       TEXT NOT NULL REFERENCES courses(id),
    kind            TEXT NOT NULL,       -- cie | see | lab | project
    name            TEXT NOT NULL,
    academic_year   TEXT NOT NULL,
    section         TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, course_id, academic_year, section, name)
);

CREATE TABLE IF NOT EXISTS questions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    assessment_id   TEXT NOT NULL REFERENCES assessments(id),
    question_no     TEXT NOT NULL,
    co_code         TEXT NOT NULL DEFAULT '',
    max_marks       REAL NOT NULL,
    -- Questions sharing a choice_group are alternatives ("attempt any two of
    -- five"). Null means compulsory. See engine/attainment.py: this changes
    -- the attainment denominator and is not cosmetic.
    choice_group    TEXT,
    choose_count    INTEGER,
    position        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, assessment_id, question_no)
);

CREATE TABLE IF NOT EXISTS marks (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    question_id  TEXT NOT NULL REFERENCES questions(id),
    roll         TEXT NOT NULL,
    mark         REAL,
    status       TEXT NOT NULL,    -- present | absent | not_attempted
    UNIQUE (tenant_id, question_id, roll)
);

CREATE TABLE IF NOT EXISTS surveys (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    course_id       TEXT REFERENCES courses(id),
    program_id      TEXT REFERENCES programs(id),
    kind            TEXT NOT NULL,       -- course_exit | alumni | employer | student
    academic_year   TEXT NOT NULL,
    scale_max       INTEGER NOT NULL DEFAULT 5,
    items           TEXT NOT NULL DEFAULT '[]',   -- JSON [{id, target_kind, target_code, text}]
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_responses (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    survey_id    TEXT NOT NULL REFERENCES surveys(id),
    -- Pseudonymous by construction: we store a per-survey hash, never a roll
    -- number, so a response cannot be traced back to a student. Indirect
    -- attainment needs counts, not identities.
    respondent   TEXT NOT NULL,
    answers      TEXT NOT NULL,        -- JSON {item_id: score}
    submitted_at TEXT NOT NULL,
    UNIQUE (tenant_id, survey_id, respondent)
);

CREATE TABLE IF NOT EXISTS attainment_runs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    course_id       TEXT REFERENCES courses(id),
    program_id      TEXT REFERENCES programs(id),
    academic_year   TEXT NOT NULL,
    scope           TEXT NOT NULL,       -- course | program
    config          TEXT NOT NULL,       -- JSON: thresholds, modes, weights
    result          TEXT NOT NULL,       -- JSON: the computed numbers
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    entity_type  TEXT NOT NULL,          -- course_matrix | attainment_run | program_sar
    entity_id    TEXT NOT NULL,
    state        TEXT NOT NULL,          -- draft | submitted | approved | locked | returned
    actor        TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL
);

-- Append-only, hash-chained. See store/audit.py.
CREATE TABLE IF NOT EXISTS audit_events (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    seq          INTEGER NOT NULL,
    at           TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL,
    UNIQUE (tenant_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_cos_course       ON cos(tenant_id, course_id);
CREATE INDEX IF NOT EXISTS ix_map_course       ON co_outcome_map(tenant_id, course_id);
CREATE INDEX IF NOT EXISTS ix_q_assessment     ON questions(tenant_id, assessment_id);
CREATE INDEX IF NOT EXISTS ix_marks_question   ON marks(tenant_id, question_id);
CREATE INDEX IF NOT EXISTS ix_runs_year        ON attainment_runs(tenant_id, academic_year);
CREATE INDEX IF NOT EXISTS ix_audit_entity     ON audit_events(tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_appr_entity      ON approvals(tenant_id, entity_type, entity_id);
"""

# Tables holding tenant-owned rows. Any SELECT against one of these must carry
# a tenant predicate; `TenantStore._rows` verifies the returned rows agree.
TENANT_TABLES = frozenset({
    "users", "programs", "psos", "courses", "cos", "co_outcome_map",
    "assessments", "questions", "marks", "surveys", "survey_responses",
    "attainment_runs", "approvals", "audit_events",
})


class CrossTenantAccess(RuntimeError):
    """A row belonging to another tenant reached a tenant-bound store."""


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str


class Database:
    """
    Connection owner. Not tenant-aware on purpose -- callers must go through
    `tenant()` to touch anything tenant-owned.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because uvicorn serves requests on a thread
        # pool; the lock below serialises writes, which is ample at the scale
        # of one college's coursework.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- raw helpers, deliberately package-private -------------------------

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    # -- tenants -----------------------------------------------------------

    def create_tenant(self, name: str, *, tenant_id: str | None = None) -> Tenant:
        tid = tenant_id or new_id()
        self._execute(
            "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tid, name, _now()),
        )
        return Tenant(tid, name)

    def list_tenants(self) -> list[Tenant]:
        return [Tenant(r["id"], r["name"])
                for r in self._query("SELECT id, name FROM tenants ORDER BY name")]

    def tenant(self, tenant_id: str) -> "TenantStore":
        rows = self._query("SELECT id FROM tenants WHERE id = ?", (tenant_id,))
        if not rows:
            raise KeyError(f"No such tenant: {tenant_id!r}")
        return TenantStore(self, tenant_id)


class TenantStore:
    """
    Every read and write is bound to one tenant. Note what is missing: no
    method takes a `tenant_id` argument, so no caller can address another
    college's data even by mistake.
    """

    def __init__(self, db: Database, tenant_id: str) -> None:
        self._db = db
        self.tenant_id = tenant_id

    # -- guarded primitives ------------------------------------------------

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        out: list[dict] = []
        for row in self._db._query(sql, params):
            d = dict(row)
            owner = d.get("tenant_id")
            if owner is not None and owner != self.tenant_id:
                raise CrossTenantAccess(
                    f"Query returned a row owned by tenant {owner!r} to a store "
                    f"bound to {self.tenant_id!r}. The query is missing a tenant "
                    f"predicate: {sql.strip().splitlines()[0]}"
                )
            out.append(d)
        return out

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def _write(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._db._execute(sql, params)

    # -- users -------------------------------------------------------------

    def add_user(self, email: str, name: str, role: str) -> str:
        from app.store.workflow import ROLES
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}; expected one of {sorted(ROLES)}")
        uid = new_id()
        self._write(
            "INSERT INTO users (id, tenant_id, email, name, role) VALUES (?,?,?,?,?)",
            (uid, self.tenant_id, email.lower().strip(), name, role),
        )
        return uid

    def user_by_email(self, email: str) -> dict | None:
        return self._one(
            "SELECT * FROM users WHERE tenant_id = ? AND email = ?",
            (self.tenant_id, email.lower().strip()),
        )

    def list_users(self) -> list[dict]:
        return self._rows(
            "SELECT * FROM users WHERE tenant_id = ? ORDER BY name", (self.tenant_id,)
        )

    # -- programs and PSOs -------------------------------------------------

    def create_program(self, code: str, name: str, framework_code: str,
                       branch: str = "") -> str:
        pid = new_id()
        self._write(
            "INSERT INTO programs (id, tenant_id, code, name, branch, framework_code) "
            "VALUES (?,?,?,?,?,?)",
            (pid, self.tenant_id, code, name, branch, framework_code),
        )
        return pid

    def get_program(self, program_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM programs WHERE tenant_id = ? AND id = ?",
            (self.tenant_id, program_id),
        )

    def program_by_code(self, code: str) -> dict | None:
        return self._one(
            "SELECT * FROM programs WHERE tenant_id = ? AND code = ?",
            (self.tenant_id, code),
        )

    def list_programs(self) -> list[dict]:
        return self._rows(
            "SELECT * FROM programs WHERE tenant_id = ? ORDER BY code", (self.tenant_id,)
        )

    def set_psos(self, program_id: str, psos: Sequence[tuple[str, str]]) -> None:
        """Replace the program's PSO set. `psos` is [(code, statement), ...]."""
        self._write(
            "DELETE FROM psos WHERE tenant_id = ? AND program_id = ?",
            (self.tenant_id, program_id),
        )
        for i, (code, statement) in enumerate(psos):
            self._write(
                "INSERT INTO psos (id, tenant_id, program_id, code, statement, position) "
                "VALUES (?,?,?,?,?,?)",
                (new_id(), self.tenant_id, program_id, code, statement, i),
            )

    def list_psos(self, program_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM psos WHERE tenant_id = ? AND program_id = ? ORDER BY position",
            (self.tenant_id, program_id),
        )

    # -- courses and COs ---------------------------------------------------

    def create_course(self, program_id: str, code: str, title: str, *,
                      semester: int = 0, credits: float = 0, ltp: str = "0-0-0",
                      scheme: str = "") -> str:
        cid = new_id()
        self._write(
            "INSERT INTO courses (id, tenant_id, program_id, code, title, semester, "
            "credits, ltp, scheme) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, self.tenant_id, program_id, code, title, semester, credits, ltp, scheme),
        )
        return cid

    def get_course(self, course_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM courses WHERE tenant_id = ? AND id = ?",
            (self.tenant_id, course_id),
        )

    def course_by_code(self, program_id: str, code: str) -> dict | None:
        return self._one(
            "SELECT * FROM courses WHERE tenant_id = ? AND program_id = ? AND code = ?",
            (self.tenant_id, program_id, code),
        )

    def list_courses(self, program_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM courses WHERE tenant_id = ? AND program_id = ? "
            "ORDER BY semester, code",
            (self.tenant_id, program_id),
        )

    def set_cos(self, course_id: str,
                cos: Sequence[tuple[str, str, list[int], str | None]]) -> None:
        """Replace a course's COs. Each entry: (code, statement, blooms, original)."""
        self._write(
            "DELETE FROM cos WHERE tenant_id = ? AND course_id = ?",
            (self.tenant_id, course_id),
        )
        for i, (code, statement, blooms, original) in enumerate(cos):
            self._write(
                "INSERT INTO cos (id, tenant_id, course_id, code, statement, "
                "blooms_levels, original_statement, position) VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), self.tenant_id, course_id, code, statement,
                 json.dumps(blooms), original, i),
            )

    def list_cos(self, course_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM cos WHERE tenant_id = ? AND course_id = ? ORDER BY position",
            (self.tenant_id, course_id),
        )
        for r in rows:
            r["blooms_levels"] = json.loads(r["blooms_levels"])
        return rows

    # -- mapping -----------------------------------------------------------

    def set_mapping(self, course_id: str, co_code: str, target_kind: str,
                    target_code: str, *, competency: int | str, level: int,
                    source: str, framework_code: str = "", evidence: str = "") -> None:
        from app.engine.mapping_rules import parse_competency
        if target_kind not in ("PO", "PSO"):
            raise ValueError(f"target_kind must be 'PO' or 'PSO', got {target_kind!r}")
        competency = int(parse_competency(competency))
        if source not in ("proposed", "override"):
            raise ValueError(f"source must be 'proposed' or 'override', got {source!r}")
        self._write(
            "INSERT INTO co_outcome_map (id, tenant_id, course_id, co_code, target_kind, "
            "target_code, framework_code, competency, level, source, evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, course_id, co_code, target_kind, target_code) "
            "DO UPDATE SET competency=excluded.competency, level=excluded.level, "
            "source=excluded.source, evidence=excluded.evidence, "
            "framework_code=excluded.framework_code",
            (new_id(), self.tenant_id, course_id, co_code, target_kind, target_code,
             framework_code, competency, level, source, evidence),
        )

    def list_mapping(self, course_id: str, target_kind: str | None = None) -> list[dict]:
        if target_kind:
            return self._rows(
                "SELECT * FROM co_outcome_map WHERE tenant_id = ? AND course_id = ? "
                "AND target_kind = ? ORDER BY co_code, target_code",
                (self.tenant_id, course_id, target_kind),
            )
        return self._rows(
            "SELECT * FROM co_outcome_map WHERE tenant_id = ? AND course_id = ? "
            "ORDER BY co_code, target_kind, target_code",
            (self.tenant_id, course_id),
        )

    def mapping_levels(self, course_id: str, target_kind: str = "PO") -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.list_mapping(course_id, target_kind):
            out.setdefault(r["co_code"], {})[r["target_code"]] = r["level"]
        return out

    def clear_mapping(self, course_id: str) -> None:
        self._write(
            "DELETE FROM co_outcome_map WHERE tenant_id = ? AND course_id = ?",
            (self.tenant_id, course_id),
        )

    # -- assessments, questions, marks -------------------------------------

    def create_assessment(self, course_id: str, *, kind: str, name: str,
                          academic_year: str, section: str = "") -> str:
        aid = new_id()
        self._write(
            "INSERT INTO assessments (id, tenant_id, course_id, kind, name, "
            "academic_year, section) VALUES (?,?,?,?,?,?,?)",
            (aid, self.tenant_id, course_id, kind, name, academic_year, section),
        )
        return aid

    def list_assessments(self, course_id: str, academic_year: str | None = None) -> list[dict]:
        if academic_year:
            return self._rows(
                "SELECT * FROM assessments WHERE tenant_id = ? AND course_id = ? "
                "AND academic_year = ? ORDER BY name",
                (self.tenant_id, course_id, academic_year),
            )
        return self._rows(
            "SELECT * FROM assessments WHERE tenant_id = ? AND course_id = ? ORDER BY name",
            (self.tenant_id, course_id),
        )

    def add_question(self, assessment_id: str, *, question_no: str, co_code: str,
                     max_marks: float, choice_group: str | None = None,
                     choose_count: int | None = None, position: int = 0) -> str:
        qid = new_id()
        self._write(
            "INSERT INTO questions (id, tenant_id, assessment_id, question_no, co_code, "
            "max_marks, choice_group, choose_count, position) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, assessment_id, question_no) DO UPDATE SET "
            "co_code=excluded.co_code, max_marks=excluded.max_marks, "
            "choice_group=excluded.choice_group, choose_count=excluded.choose_count",
            (qid, self.tenant_id, assessment_id, question_no, co_code, max_marks,
             choice_group, choose_count, position),
        )
        row = self._one(
            "SELECT id FROM questions WHERE tenant_id = ? AND assessment_id = ? "
            "AND question_no = ?",
            (self.tenant_id, assessment_id, question_no),
        )
        return row["id"] if row else qid

    def list_questions(self, assessment_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM questions WHERE tenant_id = ? AND assessment_id = ? "
            "ORDER BY position, question_no",
            (self.tenant_id, assessment_id),
        )

    def set_mark(self, question_id: str, roll: str, mark: float | None, status: str) -> None:
        if status not in ("present", "absent", "not_attempted"):
            raise ValueError(f"Unknown mark status {status!r}")
        self._write(
            "INSERT INTO marks (id, tenant_id, question_id, roll, mark, status) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT (tenant_id, question_id, roll) "
            "DO UPDATE SET mark=excluded.mark, status=excluded.status",
            (new_id(), self.tenant_id, question_id, roll, mark, status),
        )

    def list_marks(self, question_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM marks WHERE tenant_id = ? AND question_id = ? ORDER BY roll",
            (self.tenant_id, question_id),
        )

    # -- surveys -----------------------------------------------------------

    def create_survey(self, *, kind: str, academic_year: str, items: list[dict],
                      course_id: str | None = None, program_id: str | None = None,
                      scale_max: int = 5) -> str:
        sid = new_id()
        self._write(
            "INSERT INTO surveys (id, tenant_id, course_id, program_id, kind, "
            "academic_year, scale_max, items, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, self.tenant_id, course_id, program_id, kind, academic_year,
             scale_max, json.dumps(items), _now()),
        )
        return sid

    def get_survey(self, survey_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM surveys WHERE tenant_id = ? AND id = ?",
            (self.tenant_id, survey_id),
        )
        if row:
            row["items"] = json.loads(row["items"])
        return row

    def list_surveys(self, *, course_id: str | None = None,
                     program_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM surveys WHERE tenant_id = ?"
        params: list[Any] = [self.tenant_id]
        if course_id:
            sql += " AND course_id = ?"
            params.append(course_id)
        if program_id:
            sql += " AND program_id = ?"
            params.append(program_id)
        rows = self._rows(sql + " ORDER BY created_at DESC", params)
        for r in rows:
            r["items"] = json.loads(r["items"])
        return rows

    def add_response(self, survey_id: str, respondent: str, answers: dict[str, float]) -> None:
        self._write(
            "INSERT INTO survey_responses (id, tenant_id, survey_id, respondent, "
            "answers, submitted_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, survey_id, respondent) DO UPDATE SET "
            "answers=excluded.answers, submitted_at=excluded.submitted_at",
            (new_id(), self.tenant_id, survey_id, respondent, json.dumps(answers), _now()),
        )

    def list_responses(self, survey_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM survey_responses WHERE tenant_id = ? AND survey_id = ?",
            (self.tenant_id, survey_id),
        )
        for r in rows:
            r["answers"] = json.loads(r["answers"])
        return rows

    # -- attainment runs (the longitudinal record) -------------------------

    def save_run(self, *, academic_year: str, scope: str, config: dict, result: dict,
                 course_id: str | None = None, program_id: str | None = None,
                 created_by: str = "") -> str:
        rid = new_id()
        self._write(
            "INSERT INTO attainment_runs (id, tenant_id, course_id, program_id, "
            "academic_year, scope, config, result, created_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, self.tenant_id, course_id, program_id, academic_year, scope,
             json.dumps(config), json.dumps(result), _now(), created_by),
        )
        return rid

    def get_run(self, run_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM attainment_runs WHERE tenant_id = ? AND id = ?",
            (self.tenant_id, run_id),
        )
        if row:
            row["config"] = json.loads(row["config"])
            row["result"] = json.loads(row["result"])
        return row

    def list_runs(self, *, course_id: str | None = None, program_id: str | None = None,
                  scope: str | None = None) -> list[dict]:
        sql = "SELECT * FROM attainment_runs WHERE tenant_id = ?"
        params: list[Any] = [self.tenant_id]
        if course_id:
            sql += " AND course_id = ?"
            params.append(course_id)
        if program_id:
            sql += " AND program_id = ?"
            params.append(program_id)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        rows = self._rows(sql + " ORDER BY academic_year, created_at", params)
        for r in rows:
            r["config"] = json.loads(r["config"])
            r["result"] = json.loads(r["result"])
        return rows


def bootstrap(path: str | Path = ":memory:", *, tenant_name: str = "Demo Institute",
              tenant_id: str = "demo") -> tuple[Database, TenantStore]:
    """Open a database and guarantee the demo tenant exists."""
    db = Database(path)
    if not db._query("SELECT id FROM tenants WHERE id = ?", (tenant_id,)):
        db.create_tenant(tenant_name, tenant_id=tenant_id)
    return db, db.tenant(tenant_id)
