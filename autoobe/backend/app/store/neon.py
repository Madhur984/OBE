"""
The shared database: accounts, course assignments, and submitted mappings.

Everything else in this application is deliberately offline and single-user --
one person opens the page, maps a syllabus, downloads a workbook, and nothing
is kept. That is still true of the mapping engine. This module adds the one
thing a department actually needs on top of it: **many teachers, one sheet.**

Three rules shape the schema, and each of them came from the department:

  1. **One admin sees and edits everything.** Every course, every mapping,
     and the whole workbook as a single Excel file -- the same artefact they
     already keep by hand.
  2. **A teacher sees only their own subjects.** Not a filtered view of the
     whole table but a query that cannot address another teacher's course:
     the WHERE clause is on `teacher_id`, always, and it is applied in the
     query rather than in the caller.
  3. **Nothing is stored until Submit.** There is no drafts table here, and
     that absence is the feature. A mapping exists in this database only
     because a teacher pressed Submit; until then it lives in their browser
     and nowhere else. Work in progress is therefore invisible to the admin
     by construction rather than by a status flag somebody could forget to
     check.

The connection is optional on purpose. `DATABASE_URL` absent means this
module is dormant and the mapping engine still runs exactly as it did --
which is what keeps the offline test suite honest and lets the tool be used
on a laptop with no account at all.
"""
from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Where `neon link` leaves the pulled credentials. Read as a fallback so a
# developer who has linked the project needs no further setup, and never
# committed -- the generated .gitignore covers it.
#
# Searched by walking up from this module rather than at a fixed depth: `neon
# link` writes .env.local beside the directory it was run in, which is the
# repository root here and four levels above this file, and a fixed relative
# path breaks the moment the tree is rearranged.
ENV_NAMES = (".env.local", ".env")

SESSION_HOURS = 12
ADMIN = "admin"
TEACHER = "teacher"
ROLES = (ADMIN, TEACHER)

_pool = None
_schema_ready = False


class NotConfigured(RuntimeError):
    """Raised when a database route is reached with no DATABASE_URL."""


# ----------------------------------------------------------------- wiring

def database_url() -> str:
    """The Neon connection string, from the environment or a linked .env.

    An explicitly-set `DATABASE_URL` is authoritative **even when empty**.
    That is what lets the test suite say "this run has no database" on a
    developer's machine that has one linked -- without it, `.env.local` would
    be found by the walk-up below and the offline suite would quietly run
    against production.
    """
    if "DATABASE_URL" in os.environ:
        return os.environ["DATABASE_URL"].strip()
    for folder in Path(__file__).resolve().parents:
        for name in ENV_NAMES:
            path = folder / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", text, re.M)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


def is_configured() -> bool:
    return bool(database_url())


def pool():
    """A lazily-opened connection pool, or NotConfigured.

    Pooled rather than one connection per request because Neon scales its
    compute to zero: the first connection after an idle period pays a cold
    start, and a pool means the department pays it once rather than on every
    click.
    """
    global _pool
    if _pool is not None:
        return _pool
    url = database_url()
    if not url:
        raise NotConfigured(
            "No DATABASE_URL. Run `neon link` in the project directory, or set "
            "DATABASE_URL, to enable accounts and submissions.")
    from psycopg_pool import ConnectionPool          # noqa: PLC0415

    # On a long-lived server a warm spare connection is what spares the
    # department the Neon cold start. On a serverless host the opposite is
    # true: each invocation builds its own pool, so min_size=1 makes every
    # cold start block on a connection the request may never need, inside a
    # execution window measured in seconds. VERCEL is set by the platform.
    serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    _pool = ConnectionPool(url, min_size=0 if serverless else 1, max_size=8,
                           open=True, timeout=30,
                           kwargs={"connect_timeout": 20})
    # Closed deliberately at exit. Left to the garbage collector, the pool's
    # worker threads outlive the interpreter's shutdown and every run ends
    # with a PythonFinalizationError traceback that looks like a real fault.
    atexit.register(reset_pool)
    return _pool


def reset_pool() -> None:
    """Close the pool. For tests and for a changed connection string."""
    global _pool, _schema_ready
    if _pool is not None:
        try:
            _pool.close()
        except Exception:                              # noqa: BLE001
            pass
    _pool, _schema_ready = None, False


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(sql: str, params: tuple = ()) -> dict | None:
    got = _rows(sql, params)
    return got[0] if got else None


def _exec(sql: str, params: tuple = ()) -> int:
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


# ------------------------------------------------------------------ schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL CHECK (role IN ('admin', 'teacher')),
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS courses (
    id            BIGSERIAL PRIMARY KEY,
    code          TEXT NOT NULL,
    title         TEXT NOT NULL,
    -- The department's own course serial: "C204" renders its outcomes as
    -- C204.1 .. C204.5 in the sheet.
    co_prefix     TEXT NOT NULL DEFAULT '',
    academic_year TEXT NOT NULL DEFAULT '',
    semester      TEXT NOT NULL DEFAULT '',
    teacher_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code, academic_year)
);
CREATE INDEX IF NOT EXISTS courses_teacher ON courses(teacher_id);

-- A row here means a teacher pressed Submit. There is deliberately no
-- drafts table: an unsubmitted mapping is not in this database at all.
CREATE TABLE IF NOT EXISTS submissions (
    id             BIGSERIAL PRIMARY KEY,
    course_id      BIGINT NOT NULL UNIQUE REFERENCES courses(id) ON DELETE CASCADE,
    submitted_by   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    framework_code TEXT NOT NULL DEFAULT 'GAPC_V3',
    -- {cos: [{code, statement, blooms_levels}], co_levels: {co: {po: level}}}
    payload        JSONB NOT NULL
);

-- Internal CO attainment: the teacher's marks table reduced to one level per
-- CO and a course average. Same rule as submissions -- a row here means Submit
-- was pressed, and there is no drafts table behind it either.
CREATE TABLE IF NOT EXISTS attainment (
    id           BIGSERIAL PRIMARY KEY,
    course_id    BIGINT NOT NULL UNIQUE REFERENCES courses(id) ON DELETE CASCADE,
    submitted_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    t1_pct       DOUBLE PRECISION NOT NULL DEFAULT 45,
    t2_pct       DOUBLE PRECISION NOT NULL DEFAULT 60,
    students     INTEGER NOT NULL DEFAULT 0,
    -- The course average, lifted out of the payload so the dashboard can sort
    -- and average on it without unpacking JSON on every row.
    internal     DOUBLE PRECISION,
    payload      JSONB NOT NULL
);

-- Who changed what. An accreditation file is a record, and a mapping that
-- changed after submission has to be able to say who changed it.
CREATE TABLE IF NOT EXISTS audit (
    id     BIGSERIAL PRIMARY KEY,
    at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor  TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_at ON audit(at DESC);
"""


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
    _schema_ready = True


def log(actor: str, action: str, detail: dict | None = None) -> None:
    _exec("INSERT INTO audit (actor, action, detail) VALUES (%s, %s, %s)",
          (actor, action, json.dumps(detail or {})))


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    """scrypt from the standard library -- no dependency, and memory-hard.

    Stored as `scrypt$n$r$p$salt$key` so the parameters travel with the hash
    and can be raised later without invalidating existing accounts.
    """
    if len(password) < 8:
        raise ValueError("A password needs at least 8 characters.")
    n, r, p = 2 ** 14, 8, 1
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, key_hex = stored.split("$")
        if algo != "scrypt":
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                             n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    # Constant time: a timing difference here leaks which usernames exist.
    return hmac.compare_digest(key.hex(), key_hex)


# -------------------------------------------------------------------- users

@dataclass(frozen=True)
class User:
    id: int
    username: str
    full_name: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username,
                "full_name": self.full_name, "email": self.email,
                "role": self.role}


def create_user(*, username: str, password: str, role: str,
                full_name: str = "", email: str = "") -> dict:
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", username):
        raise ValueError("Username: 3-40 characters, letters, digits, . _ -")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {', '.join(ROLES)}")
    row = _one(
        """INSERT INTO users (username, full_name, email, role, password_hash)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (username) DO NOTHING
           RETURNING id, username, full_name, email, role, is_active""",
        (username, full_name.strip(), email.strip(), role,
         hash_password(password)))
    if row is None:
        raise ValueError(f"The username {username!r} is already taken.")
    return row


def list_users() -> list[dict]:
    return _rows(
        """SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                  u.created_at,
                  count(c.id) AS courses
             FROM users u LEFT JOIN courses c ON c.teacher_id = u.id
            GROUP BY u.id ORDER BY u.role, u.username""")


def set_user_active(user_id: int, active: bool) -> bool:
    return _exec("UPDATE users SET is_active = %s WHERE id = %s",
                 (active, user_id)) > 0


def set_password(user_id: int, password: str) -> bool:
    changed = _exec("UPDATE users SET password_hash = %s WHERE id = %s",
                    (hash_password(password), user_id)) > 0
    if changed:
        # Every existing session for that account stops working, which is the
        # point of changing a password.
        _exec("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    return changed


def delete_user(user_id: int) -> bool:
    return _exec("DELETE FROM users WHERE id = %s", (user_id,)) > 0


def change_own_password(user_id: int, current: str, new: str) -> bool:
    """Change a password having proved you know the old one.

    Separate from `set_password`, which is the administrator's reset and
    deliberately does not ask for the current one.
    """
    row = _one("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    if row is None or not verify_password(current or "", row["password_hash"]):
        return False
    set_password(user_id, new)
    return True


def count_admins() -> int:
    row = _one("SELECT count(*) AS n FROM users "
               "WHERE role = 'admin' AND is_active")
    return int(row["n"]) if row else 0


# ----------------------------------------------------------------- sessions

def login(username: str, password: str) -> tuple[str, User] | None:
    row = _one(
        """SELECT id, username, full_name, email, role, password_hash, is_active
             FROM users WHERE username = %s""", ((username or "").strip().lower(),))
    # Verify even when the user does not exist, so a wrong username and a
    # wrong password take the same time to answer.
    stored = row["password_hash"] if row else hash_password("not-a-real-password")
    if not verify_password(password or "", stored) or not row or not row["is_active"]:
        return None
    token = secrets.token_urlsafe(32)
    _exec("INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
          (token, row["id"], datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)))
    return token, User(row["id"], row["username"], row["full_name"],
                       row["email"], row["role"])


def user_for_token(token: str) -> User | None:
    if not token:
        return None
    row = _one(
        """SELECT u.id, u.username, u.full_name, u.email, u.role
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = %s AND s.expires_at > now() AND u.is_active""",
        (token,))
    if not row:
        return None
    return User(row["id"], row["username"], row["full_name"], row["email"],
                row["role"])


def logout(token: str) -> None:
    if token:
        _exec("DELETE FROM sessions WHERE token = %s", (token,))


def purge_expired_sessions() -> int:
    return _exec("DELETE FROM sessions WHERE expires_at <= now()")


# ------------------------------------------------------------------ courses

def create_course(*, code: str, title: str, co_prefix: str = "",
                  academic_year: str = "", semester: str = "",
                  teacher_id: int | None = None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise ValueError("A course needs a code and a title.")
    row = _one(
        """INSERT INTO courses (code, title, co_prefix, academic_year,
                                semester, teacher_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (code, academic_year) DO NOTHING
           RETURNING id, code, title, co_prefix, academic_year, semester,
                     teacher_id""",
        (code, title.strip(), co_prefix.strip(), academic_year.strip(),
         semester.strip(), teacher_id))
    if row is None:
        raise ValueError(f"{code} already exists for {academic_year or 'this year'}.")
    return row


def update_course(course_id: int, **fields) -> bool:
    allowed = ("code", "title", "co_prefix", "academic_year", "semester",
               "teacher_id")
    sets, params = [], []
    for key in allowed:
        if key in fields:
            sets.append(f"{key} = %s")
            params.append(fields[key])
    if not sets:
        return False
    params.append(course_id)
    return _exec(f"UPDATE courses SET {', '.join(sets)} WHERE id = %s",
                 tuple(params)) > 0


def delete_course(course_id: int) -> bool:
    return _exec("DELETE FROM courses WHERE id = %s", (course_id,)) > 0


_COURSE_SELECT = """
    SELECT c.id, c.code, c.title, c.co_prefix, c.academic_year, c.semester,
           c.teacher_id, u.username AS teacher_username,
           u.full_name AS teacher_name,
           s.id IS NOT NULL AS submitted,
           s.submitted_at, s.updated_at, s.framework_code,
           -- The other half of a teacher's work on the same subject. Joined
           -- here so one query answers "where is this course up to?" for both
           -- the mapping and the attainment.
           a.id IS NOT NULL AS attainment_submitted,
           a.internal AS attainment_internal,
           a.updated_at AS attainment_updated_at
      FROM courses c
      LEFT JOIN users u ON u.id = c.teacher_id
      LEFT JOIN submissions s ON s.course_id = c.id
      LEFT JOIN attainment a ON a.course_id = c.id
"""


def list_courses() -> list[dict]:
    """Every course. Admin only -- the teacher view has its own query."""
    return _rows(_COURSE_SELECT + " ORDER BY c.academic_year DESC, c.code")


def courses_for_teacher(teacher_id: int) -> list[dict]:
    """A teacher's own courses, and no way to ask for anyone else's.

    The teacher id is a bound parameter of this query rather than a filter the
    caller applies afterwards, so a route that forgets to check gets an empty
    list instead of somebody else's subjects.
    """
    return _rows(_COURSE_SELECT + " WHERE c.teacher_id = %s"
                 " ORDER BY c.academic_year DESC, c.code", (teacher_id,))


def get_course(course_id: int) -> dict | None:
    return _one(_COURSE_SELECT + " WHERE c.id = %s", (course_id,))


def teacher_owns(course_id: int, teacher_id: int) -> bool:
    return _one("SELECT 1 AS ok FROM courses WHERE id = %s AND teacher_id = %s",
                (course_id, teacher_id)) is not None


# -------------------------------------------------------------- submissions

def submit_mapping(*, course_id: int, user_id: int, framework_code: str,
                   payload: dict) -> dict:
    """Store a mapping. This is the only way a mapping enters the database.

    Re-submitting replaces the previous one and records who changed it; the
    original `submitted_at` is kept, because when the work first arrived is a
    different fact from when it was last edited.
    """
    row = _one(
        """INSERT INTO submissions (course_id, submitted_by, updated_by,
                                    framework_code, payload)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (course_id) DO UPDATE
              SET payload = EXCLUDED.payload,
                  framework_code = EXCLUDED.framework_code,
                  updated_at = now(),
                  updated_by = EXCLUDED.updated_by
           RETURNING id, course_id, submitted_at, updated_at""",
        (course_id, user_id, user_id, framework_code, json.dumps(payload)))
    return row or {}


def get_submission(course_id: int) -> dict | None:
    return _one(
        """SELECT s.id, s.course_id, s.framework_code, s.payload,
                  s.submitted_at, s.updated_at,
                  c.code, c.title, c.co_prefix, c.academic_year,
                  bu.username AS submitted_by, uu.username AS updated_by
             FROM submissions s
             JOIN courses c ON c.id = s.course_id
             LEFT JOIN users bu ON bu.id = s.submitted_by
             LEFT JOIN users uu ON uu.id = s.updated_by
            WHERE s.course_id = %s""", (course_id,))


def all_submissions() -> list[dict]:
    """Every submitted mapping, newest first. The admin's dashboard."""
    return _rows(
        """SELECT s.id, s.course_id, s.framework_code, s.payload,
                  s.submitted_at, s.updated_at,
                  c.code, c.title, c.co_prefix, c.academic_year, c.semester,
                  bu.username AS submitted_by, bu.full_name AS submitted_by_name
             FROM submissions s
             JOIN courses c ON c.id = s.course_id
             LEFT JOIN users bu ON bu.id = s.submitted_by
            ORDER BY s.updated_at DESC""")


def submit_attainment(*, course_id: int, user_id: int, t1_pct: float,
                      t2_pct: float, internal: float | None, students: int,
                      payload: dict) -> dict:
    """Store a course's internal CO attainment. The only way it gets in."""
    row = _one(
        """INSERT INTO attainment (course_id, submitted_by, updated_by, t1_pct,
                                  t2_pct, internal, students, payload)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (course_id) DO UPDATE
              SET payload = EXCLUDED.payload, t1_pct = EXCLUDED.t1_pct,
                  t2_pct = EXCLUDED.t2_pct, internal = EXCLUDED.internal,
                  students = EXCLUDED.students, updated_at = now(),
                  updated_by = EXCLUDED.updated_by
           RETURNING id, course_id, submitted_at, updated_at, internal""",
        (course_id, user_id, user_id, t1_pct, t2_pct, internal, students,
         json.dumps(payload)))
    return row or {}


def get_attainment(course_id: int) -> dict | None:
    return _one(
        """SELECT a.id, a.course_id, a.t1_pct, a.t2_pct, a.internal, a.students,
                  a.payload, a.submitted_at, a.updated_at,
                  c.code, c.title, c.academic_year,
                  bu.username AS submitted_by, uu.username AS updated_by
             FROM attainment a
             JOIN courses c ON c.id = a.course_id
             LEFT JOIN users bu ON bu.id = a.submitted_by
             LEFT JOIN users uu ON uu.id = a.updated_by
            WHERE a.course_id = %s""", (course_id,))


def all_attainment() -> list[dict]:
    return _rows(
        """SELECT a.id, a.course_id, a.t1_pct, a.t2_pct, a.internal, a.students,
                  a.payload, a.submitted_at, a.updated_at,
                  c.code, c.title, c.academic_year,
                  bu.username AS submitted_by, bu.full_name AS submitted_by_name
             FROM attainment a
             JOIN courses c ON c.id = a.course_id
             LEFT JOIN users bu ON bu.id = a.submitted_by
            ORDER BY a.updated_at DESC""")


def delete_attainment(course_id: int) -> bool:
    return _exec("DELETE FROM attainment WHERE course_id = %s", (course_id,)) > 0


def courses_with_mapping_and_attainment() -> list[dict]:
    """The courses that have both halves, joined.

    A CO → PO mapping says which outcomes serve which programme outcome. A CO
    attainment says how well each outcome was actually met. Neither alone is
    PO attainment; the product of the two is, and this is the only query that
    puts them in the same row.

    An INNER JOIN on purpose. A course with a mapping and no marks, or marks
    and no mapping, contributes nothing to PO attainment and would quietly
    drag a programme average toward whichever half it has. Those courses are
    counted separately by `attainment_readiness` so the gap is visible rather
    than absorbed.
    """
    return _rows(
        """SELECT c.id AS course_id, c.code, c.title, c.academic_year,
                  s.framework_code, s.payload -> 'co_levels' AS co_levels,
                  a.payload -> 'cos'          AS co_attainment,
                  a.internal
             FROM courses c
             JOIN submissions s ON s.course_id = c.id
             JOIN attainment  a ON a.course_id = c.id
            ORDER BY c.academic_year DESC, c.code""")


def attainment_readiness() -> dict:
    """How many courses have each half, so a missing one is a visible fact."""
    row = _one(
        """SELECT (SELECT count(*) FROM courses) AS courses,
                  (SELECT count(*) FROM submissions) AS mapped,
                  (SELECT count(*) FROM attainment) AS measured,
                  (SELECT count(*) FROM submissions s
                     JOIN attainment a ON a.course_id = s.course_id) AS both""")
    got = dict(row or {})
    got["mapping_only"] = max(0, int(got.get("mapped", 0)) - int(got.get("both", 0)))
    got["marks_only"] = max(0, int(got.get("measured", 0)) - int(got.get("both", 0)))
    return got


def programme_po_coverage(po_codes: list[str]) -> dict:
    """How the whole programme sits against each PO, across everything submitted.

    This is the number a head of department is actually asked for. A course
    block answers "does this subject serve PO4?"; only the union of every
    submitted block answers "does this *programme* serve PO4?", and a PO that
    no course reaches is the finding a peer team opens the file to look for.

    Averaged the way the department's own sheet averages -- over the outcomes
    that map a PO, not over every outcome in the course -- and then over the
    courses that reach it. A course that leaves PO6 blank does not drag the
    programme's PO6 average down; it simply is not part of it, and is counted
    separately as a gap.
    """
    rows = _rows("SELECT c.code, c.title, s.payload FROM submissions s "
                 "JOIN courses c ON c.id = s.course_id")
    per_po: dict[str, list[float]] = {po: [] for po in po_codes}
    for row in rows:
        levels = (row.get("payload") or {}).get("co_levels") or {}
        for po in po_codes:
            vals = [int(v) for co in levels.values()
                    if (v := (co or {}).get(po)) and int(v) > 0]
            if vals:
                per_po[po].append(sum(vals) / len(vals))
    out = []
    for po in po_codes:
        vals = per_po[po]
        out.append({
            "po_code": po,
            "courses": len(vals),
            "average": round(sum(vals) / len(vals), 2) if vals else None,
        })
    return {"courses": len(rows), "pos": out,
            "untouched": [p["po_code"] for p in out if not p["courses"]]}


def delete_submission(course_id: int) -> bool:
    return _exec("DELETE FROM submissions WHERE course_id = %s", (course_id,)) > 0


def stats() -> dict[str, Any]:
    row = _one(
        """SELECT (SELECT count(*) FROM users WHERE role = 'teacher') AS teachers,
                  (SELECT count(*) FROM courses) AS courses,
                  (SELECT count(*) FROM submissions) AS submitted,
                  (SELECT count(*) FROM attainment) AS attainment_done""")
    got = dict(row or {})
    got["awaiting"] = max(0, int(got.get("courses", 0)) - int(got.get("submitted", 0)))
    got["attainment_awaiting"] = max(
        0, int(got.get("courses", 0)) - int(got.get("attainment_done", 0)))
    return got
