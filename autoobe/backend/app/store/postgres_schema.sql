-- AutoOBE -- PostgreSQL schema with row-level security.
--
-- SQLite is the default and the one the test suite exercises (see db.py).
-- This file is the deployed path: same tables, plus real RLS so tenant
-- isolation is enforced by the database rather than only by the application.
--
-- HONEST STATUS: this DDL has not been run against a live PostgreSQL server
-- in this repository. It is reviewed, not exercised. Run it against a scratch
-- database and confirm the policy tests below before trusting it with a
-- college's student marks.
--
-- The application connects as a role that is NOT the table owner, because
-- table owners bypass RLS unless FORCE ROW LEVEL SECURITY is set. Both are
-- applied here, belt and braces.
--
-- Per request, the app issues:
--     SET LOCAL app.tenant_id = '<tenant uuid>';
-- inside the transaction. SET LOCAL, not SET: a pooled connection must not
-- carry one college's tenant id into the next request.

BEGIN;

CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL REFERENCES tenants(id),
    email      TEXT NOT NULL,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('faculty','hod','coordinator','admin')),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS programs (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(id),
    code           TEXT NOT NULL,
    name           TEXT NOT NULL,
    branch         TEXT NOT NULL DEFAULT '',
    framework_code TEXT NOT NULL,
    UNIQUE (tenant_id, code)
);

CREATE TABLE IF NOT EXISTS psos (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL REFERENCES tenants(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    code       TEXT NOT NULL,
    statement  TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, program_id, code)
);

CREATE TABLE IF NOT EXISTS courses (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL REFERENCES tenants(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    code       TEXT NOT NULL,
    title      TEXT NOT NULL,
    semester   INTEGER NOT NULL DEFAULT 0,
    credits    REAL NOT NULL DEFAULT 0,
    ltp        TEXT NOT NULL DEFAULT '0-0-0',
    scheme     TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, program_id, code)
);

CREATE TABLE IF NOT EXISTS cos (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(id),
    course_id          TEXT NOT NULL REFERENCES courses(id),
    code               TEXT NOT NULL,
    statement          TEXT NOT NULL,
    blooms_levels      JSONB NOT NULL DEFAULT '[]'::jsonb,
    original_statement TEXT,
    position           INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, course_id, code)
);

CREATE TABLE IF NOT EXISTS co_outcome_map (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(id),
    course_id      TEXT NOT NULL REFERENCES courses(id),
    co_code        TEXT NOT NULL,
    target_kind    TEXT NOT NULL CHECK (target_kind IN ('PO','PSO')),
    target_code    TEXT NOT NULL,
    framework_code TEXT NOT NULL DEFAULT '',
    competency     TEXT NOT NULL,
    level          INTEGER NOT NULL CHECK (level BETWEEN 0 AND 3),
    source         TEXT NOT NULL CHECK (source IN ('proposed','override')),
    evidence       TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, course_id, co_code, target_kind, target_code)
);

CREATE TABLE IF NOT EXISTS assessments (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    course_id     TEXT NOT NULL REFERENCES courses(id),
    kind          TEXT NOT NULL,
    name          TEXT NOT NULL,
    academic_year TEXT NOT NULL,
    section       TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, course_id, academic_year, section, name)
);

CREATE TABLE IF NOT EXISTS questions (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    assessment_id TEXT NOT NULL REFERENCES assessments(id),
    question_no   TEXT NOT NULL,
    co_code       TEXT NOT NULL DEFAULT '',
    max_marks     REAL NOT NULL CHECK (max_marks > 0),
    choice_group  TEXT,
    choose_count  INTEGER,
    position      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, assessment_id, question_no)
);

CREATE TABLE IF NOT EXISTS marks (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    roll        TEXT NOT NULL,
    mark        REAL,
    status      TEXT NOT NULL CHECK (status IN ('present','absent','not_attempted')),
    UNIQUE (tenant_id, question_id, roll)
);

CREATE TABLE IF NOT EXISTS surveys (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    course_id     TEXT REFERENCES courses(id),
    program_id    TEXT REFERENCES programs(id),
    kind          TEXT NOT NULL,
    academic_year TEXT NOT NULL,
    scale_max     INTEGER NOT NULL DEFAULT 5,
    items         JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS survey_responses (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    survey_id    TEXT NOT NULL REFERENCES surveys(id),
    respondent   TEXT NOT NULL,
    answers      JSONB NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, survey_id, respondent)
);

CREATE TABLE IF NOT EXISTS attainment_runs (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    course_id     TEXT REFERENCES courses(id),
    program_id    TEXT REFERENCES programs(id),
    academic_year TEXT NOT NULL,
    scope         TEXT NOT NULL,
    config        JSONB NOT NULL,
    result        JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    state       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    seq         BIGINT NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL,
    UNIQUE (tenant_id, seq)
);

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
-- One policy shape for every tenant-owned table. current_setting(..., true)
-- returns NULL rather than erroring when the GUC is unset, and NULL = tenant_id
-- is never true -- so a request that forgot to set the tenant sees zero rows
-- instead of everyone's rows. That failure direction is the important one.

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users','programs','psos','courses','cos','co_outcome_map','assessments',
        'questions','marks','surveys','survey_responses','attainment_runs',
        'approvals','audit_events'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation ON %I
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        $f$, t);
    END LOOP;
END $$;

-- The audit log is append-only at the database level, not merely by
-- convention: revoking UPDATE and DELETE means a compromised application
-- cannot quietly rewrite history even though it can insert into it.
REVOKE UPDATE, DELETE ON audit_events FROM PUBLIC;

CREATE INDEX IF NOT EXISTS ix_cos_course     ON cos(tenant_id, course_id);
CREATE INDEX IF NOT EXISTS ix_map_course     ON co_outcome_map(tenant_id, course_id);
CREATE INDEX IF NOT EXISTS ix_q_assessment   ON questions(tenant_id, assessment_id);
CREATE INDEX IF NOT EXISTS ix_marks_question ON marks(tenant_id, question_id);
CREATE INDEX IF NOT EXISTS ix_runs_year      ON attainment_runs(tenant_id, academic_year);
CREATE INDEX IF NOT EXISTS ix_audit_entity   ON audit_events(tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_appr_entity    ON approvals(tenant_id, entity_type, entity_id);

COMMIT;

-- ---------------------------------------------------------------------------
-- Verify the policies before trusting them. Run as the application role:
--
--   BEGIN;
--   SET LOCAL app.tenant_id = 'college-a';
--   SELECT count(*) FROM courses;          -- only college A's courses
--   SET LOCAL app.tenant_id = 'college-b';
--   SELECT count(*) FROM courses;          -- only college B's courses
--   RESET app.tenant_id;
--   SELECT count(*) FROM courses;          -- must be 0, NOT everything
--   ROLLBACK;
--
-- The third check is the one that matters. If it returns rows, the
-- application role owns the tables and is bypassing RLS.
-- ---------------------------------------------------------------------------
