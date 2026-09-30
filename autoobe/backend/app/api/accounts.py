"""
Accounts, course assignment, and submitted mappings.

The mapping engine underneath this is single-user and stateless. This router
is what turns it into something a department runs: one admin who holds the
whole sheet, teachers who hold one subject each, and a Submit button that is
the only door between the two.

**Access control is enforced in the query, not in the route.** A teacher's
course list is `WHERE teacher_id = %s` with their own id bound; a teacher
submitting a mapping is checked against `teacher_owns` before anything is
written. A route that forgot its guard would return an empty list rather than
another teacher's subject. That is deliberate -- `workspace.py` above this
takes its actor from a header and trusts it, and this module does not.

**Nothing is stored until Submit.** There is no draft endpoint here, and no
drafts table behind it. A mapping in progress lives in the teacher's browser;
the database learns about it when they press Submit and not before.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter, Cookie, Depends, File, HTTPException, Response, UploadFile,
)
from pydantic import BaseModel, Field

from app.store import neon

router = APIRouter(prefix="/api", tags=["accounts"])

COOKIE = "autoobe_session"


# ---------------------------------------------------------------- plumbing

def _db():
    """Fail with a useful sentence rather than a stack trace when unlinked."""
    try:
        neon.ensure_schema()
    except neon.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"The database is not reachable: {exc}") from None


def current_user(
    session: Annotated[str | None, Cookie(alias=COOKIE)] = None,
) -> neon.User:
    _db()
    user = neon.user_for_token(session or "")
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def require_admin(
    user: Annotated[neon.User, Depends(current_user)],
) -> neon.User:
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the administrator can do that.")
    return user


AdminDep = Annotated[neon.User, Depends(require_admin)]
UserDep = Annotated[neon.User, Depends(current_user)]


# --------------------------------------------------------------------- auth

class LoginRequest(BaseModel):
    username: str
    password: str


class BootstrapRequest(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str = ""


@router.get("/auth/status")
def auth_status() -> dict:
    """What the sign-in page needs before anyone has signed in.

    Three states, not two, and the distinction is the whole point of this
    route. Neon scales its compute to zero, so the first request after an idle
    spell waits several seconds while the database wakes up -- and an earlier
    version of this reported that as `configured: false`, which the page drew
    as a dead end reading "The database is not connected". It was connected;
    it was asleep. A department that saw that once had no way to tell the
    difference from a broken install.

      unlinked    -- there is no DATABASE_URL at all. A person must act.
      waking      -- there is one and it did not answer in time. Retry.
      ready       -- signed-in traffic will work.
    """
    if not neon.is_configured():
        return {"state": "unlinked", "configured": False, "needs_bootstrap": False,
                "detail": "No DATABASE_URL. Run `neon link` in the project "
                          "directory to connect this install to its database."}
    try:
        neon.ensure_schema()
        return {"state": "ready", "configured": True,
                "needs_bootstrap": neon.count_admins() == 0}
    except Exception as exc:                            # noqa: BLE001
        # Configured stays true: the install is fine, this request was not.
        return {"state": "waking", "configured": True, "needs_bootstrap": False,
                "detail": f"The database did not answer in time: {exc}"}


@router.post("/auth/bootstrap")
def bootstrap(req: BootstrapRequest, response: Response) -> dict:
    """Create the first administrator. **Not reachable from the sign-in page.**

    The department asked for a page that signs people in and does nothing
    else: the administrator hands out every account, including their own,
    which is created on the machine with `tools/create_admin.py`.

    This is kept only so an installation that has somehow lost every admin can
    be recovered by a request from the machine itself, and it is closed the
    moment one exists. Nothing in the UI calls it.
    """
    _db()
    if neon.count_admins() > 0:
        raise HTTPException(
            status_code=409,
            detail="An administrator already exists. Ask them for an account.")
    try:
        neon.create_user(username=req.username, password=req.password,
                         role=neon.ADMIN, full_name=req.full_name,
                         email=req.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    got = neon.login(req.username, req.password)
    if got is None:                                     # pragma: no cover
        raise HTTPException(status_code=500, detail="Account created but sign-in failed.")
    token, user = got
    _set_cookie(response, token)
    neon.log(user.username, "bootstrap_admin", {"username": user.username})
    return {"user": user.to_dict()}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax",
        max_age=neon.SESSION_HOURS * 3600, path="/")


@router.post("/auth/login")
def login(req: LoginRequest, response: Response) -> dict:
    _db()
    got = neon.login(req.username, req.password)
    if got is None:
        # One message for both wrong-username and wrong-password: which of the
        # two it was is not the signing-in party's business.
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    token, user = got
    _set_cookie(response, token)
    neon.purge_expired_sessions()
    return {"user": user.to_dict()}


@router.post("/auth/logout")
def logout(response: Response,
           session: Annotated[str | None, Cookie(alias=COOKIE)] = None) -> dict:
    if session and neon.is_configured():
        try:
            neon.logout(session)
        except Exception:                               # noqa: BLE001
            pass
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: UserDep) -> dict:
    return {"user": user.to_dict()}


# ------------------------------------------------------------------- admin

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = neon.TEACHER
    full_name: str = ""
    email: str = ""


class PasswordReset(BaseModel):
    password: str


class CourseIn(BaseModel):
    code: str
    title: str
    co_prefix: str = ""
    academic_year: str = ""
    semester: str = ""
    teacher_id: int | None = None


class CoursePatch(BaseModel):
    code: str | None = None
    title: str | None = None
    co_prefix: str | None = None
    academic_year: str | None = None
    semester: str | None = None
    # Explicit null unassigns. Absent leaves the assignment alone.
    teacher_id: int | None = None
    unassign: bool = False


@router.get("/admin/overview")
def overview(admin: AdminDep) -> dict:
    return {"stats": neon.stats(), "courses": neon.list_courses(),
            "users": neon.list_users()}


@router.get("/admin/users")
def get_users(admin: AdminDep) -> dict:
    return {"users": neon.list_users()}


@router.post("/admin/users")
def add_user(req: UserCreate, admin: AdminDep) -> dict:
    try:
        row = neon.create_user(username=req.username, password=req.password,
                               role=req.role, full_name=req.full_name,
                               email=req.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    neon.log(admin.username, "create_user",
             {"username": row["username"], "role": row["role"]})
    return {"user": row}


@router.post("/admin/users/{user_id}/password")
def reset_password(user_id: int, req: PasswordReset, admin: AdminDep) -> dict:
    try:
        ok = neon.set_password(user_id, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not ok:
        raise HTTPException(status_code=404, detail="No such account.")
    neon.log(admin.username, "reset_password", {"user_id": user_id})
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
def remove_user(user_id: int, admin: AdminDep) -> dict:
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete the account you are signed in with.")
    if not neon.delete_user(user_id):
        raise HTTPException(status_code=404, detail="No such account.")
    neon.log(admin.username, "delete_user", {"user_id": user_id})
    return {"ok": True}


@router.get("/admin/courses")
def get_courses(admin: AdminDep) -> dict:
    return {"courses": neon.list_courses()}


@router.post("/admin/courses")
def add_course(req: CourseIn, admin: AdminDep) -> dict:
    try:
        row = neon.create_course(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    neon.log(admin.username, "create_course",
             {"code": row["code"], "teacher_id": row.get("teacher_id")})
    return {"course": row}


@router.patch("/admin/courses/{course_id}")
def edit_course(course_id: int, req: CoursePatch, admin: AdminDep) -> dict:
    fields = {k: v for k, v in req.model_dump(exclude_unset=True).items()
              if k != "unassign" and v is not None}
    if req.unassign:
        fields["teacher_id"] = None
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    if not neon.update_course(course_id, **fields):
        raise HTTPException(status_code=404, detail="No such course.")
    neon.log(admin.username, "update_course",
             {"course_id": course_id, "fields": sorted(fields)})
    return {"course": neon.get_course(course_id)}


@router.delete("/admin/courses/{course_id}")
def remove_course(course_id: int, admin: AdminDep) -> dict:
    """Deleting a course deletes its submitted mapping with it (ON DELETE
    CASCADE), which is what "delete the row from the sheet" means."""
    if not neon.delete_course(course_id):
        raise HTTPException(status_code=404, detail="No such course.")
    neon.log(admin.username, "delete_course", {"course_id": course_id})
    return {"ok": True}


@router.get("/admin/submissions")
def get_submissions(admin: AdminDep) -> dict:
    """Every mapping a teacher has submitted. The admin's dashboard."""
    return {"submissions": neon.all_submissions()}


@router.delete("/admin/submissions/{course_id}")
def remove_submission(course_id: int, admin: AdminDep) -> dict:
    """Send a mapping back: the course stays, its mapping is gone, and the
    teacher's dashboard shows it as not yet submitted again."""
    if not neon.delete_submission(course_id):
        raise HTTPException(status_code=404, detail="Nothing submitted for that course.")
    neon.log(admin.username, "delete_submission", {"course_id": course_id})
    return {"ok": True}


class AdminEdit(BaseModel):
    framework_code: str = "GAPC_V3"
    payload: dict = Field(default_factory=dict)


@router.put("/admin/submissions/{course_id}")
def edit_submission(course_id: int, req: AdminEdit, admin: AdminDep) -> dict:
    """The admin's edit. Recorded against them, not against the teacher who
    submitted it -- `submitted_by` and `updated_by` are separate columns for
    exactly this."""
    if neon.get_course(course_id) is None:
        raise HTTPException(status_code=404, detail="No such course.")
    row = neon.submit_mapping(course_id=course_id, user_id=admin.id,
                              framework_code=req.framework_code,
                              payload=req.payload)
    neon.log(admin.username, "edit_submission", {"course_id": course_id})
    return {"submission": row}


class BulkCourses(BaseModel):
    courses: list[CourseIn] = Field(default_factory=list)


@router.post("/admin/courses/bulk")
def add_courses(req: BulkCourses, admin: AdminDep) -> dict:
    """Add many subjects in one go, and say exactly which ones did not take.

    A department's sheet is forty-odd subjects. Typing them one at a time is
    the difference between this being used and not, so the admin page offers
    the whole AKTU catalogue and a paste box, and both arrive here.

    Partial success is the honest answer: a duplicate code should not throw
    away the thirty rows that were fine, so each is attempted on its own and
    the failures come back with their reasons.
    """
    added, skipped = [], []
    for c in req.courses:
        try:
            added.append(neon.create_course(**c.model_dump()))
        except ValueError as exc:
            skipped.append({"code": c.code, "reason": str(exc)})
    neon.log(admin.username, "bulk_courses",
             {"added": len(added), "skipped": len(skipped)})
    return {"added": added, "skipped": skipped}


@router.get("/admin/catalogue-search")
def catalogue_search(admin: AdminDep, q: str = "", limit: int = 25) -> dict:
    """Search the bundled AKTU catalogue so a subject is picked, not typed.

    The 749 courses are already on disk for the syllabus corroboration; this
    is the same data answering a different question -- "which subject am I
    adding to the sheet?" -- with its code, title and course kind already
    right.
    """
    from app.engine.aktu_catalogue import load_catalogue      # noqa: PLC0415

    needle = " ".join((q or "").lower().split())
    if not needle:
        return {"results": []}
    out = []
    for c in load_catalogue():
        hay = f"{c.code} {c.title}".lower()
        if all(w in hay for w in needle.split()):
            out.append({"code": c.code, "title": c.title, "kind": c.kind,
                        "year": c.year, "units": c.units})
        if len(out) >= max(1, min(limit, 100)):
            break
    return {"results": out}


@router.get("/admin/po-attainment")
def po_attainment(admin: AdminDep, framework_code: str = "GAPC_V3",
                  target: float = 2.0) -> dict:
    """
    PO attainment: the mapping and the marks, multiplied.

    This is the number the whole exercise is for -- SAR Table 3.8.1, and the
    input to Criterion 8. Everything before it is a half:

        PO(j) = Σ attainment(COᵢ) × weight(COᵢ, POⱼ) ÷ Σ weight(COᵢ, POⱼ)

    per course, and then the mean over the courses that address that PO. A PO
    no course addresses comes back as null rather than zero, because "not
    taught here" and "taught and not attained" are different findings and a
    peer team reads them differently.

    `target` is the department's, not ours -- 2.0 out of 3 is the common one
    and it is a parameter because it is a decision.
    """
    from app.engine.attainment import (                     # noqa: PLC0415
        compute_po_attainment_for_course)
    from app.engine.cqi import generate_po_cqi, summarise    # noqa: PLC0415
    from app.engine.frameworks import get_framework          # noqa: PLC0415

    try:
        fw = get_framework(framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    po_codes = list(fw.po_codes)

    rows = neon.courses_with_mapping_and_attainment()
    per_course, courses = {}, []
    for r in rows:
        # {CO: level} out of the attainment payload, {CO: {PO: weight}} out of
        # the mapping. The CO codes have to agree, and where a teacher renamed
        # one between the two submissions it simply carries no weight.
        co_att = {c["co_code"]: float(c["attainment"])
                  for c in (r["co_attainment"] or [])}
        weights = {co: {po: int(v) for po, v in (row or {}).items()}
                   for co, row in (r["co_levels"] or {}).items()}
        got = compute_po_attainment_for_course(co_att, weights, po_codes)
        per_course[r["code"]] = got
        courses.append({
            "course_id": r["course_id"], "code": r["code"], "title": r["title"],
            "academic_year": r["academic_year"], "internal": r["internal"],
            "po": got,
            "cos": len(co_att),
            # A CO that was mapped but never measured, or measured and never
            # mapped, is a real mismatch a teacher should be told about.
            "unmatched_cos": sorted(set(weights) ^ set(co_att)),
        })

    # The programme row: the mean over the courses that address each PO.
    direct: dict[str, float | None] = {}
    for po in po_codes:
        vals = [v[po] for v in per_course.values() if v.get(po) is not None]
        direct[po] = round(sum(vals) / len(vals), 2) if vals else None

    records = generate_po_cqi(direct, target=target)
    return {
        "framework_code": fw.code, "target": target,
        "po_codes": po_codes, "po_titles": fw.po_titles,
        "courses": courses,
        "direct_attainment": direct,
        "untouched": [p for p, v in direct.items() if v is None],
        "below_target": [p for p, v in direct.items()
                         if v is not None and v < target],
        "cqi": [
            {"scope": r.scope, "code": r.code, "attained": r.attained,
             "target": r.target, "gap": r.gap, "severity": r.severity.value,
             "action": r.drafted_action, "rationale": r.rationale}
            for r in records
        ],
        "cqi_summary": summarise(records),
        "readiness": neon.attainment_readiness(),
    }


@router.get("/admin/sar")
def sar_tables(admin: AdminDep, framework_code: str = "GAPC_V3",
               target: float = 2.0) -> Response:
    """
    The closing loop as the annexures a SAR is made of.

    Three tables in one workbook, from the same submitted data the dashboard
    shows -- so what a peer team reads and what the department sees are the
    same numbers, not two exports that drifted:

      * **Table 3.8.1** -- courses × POs with the Direct Attainment row.
      * **CQI actions** -- every PO under target, its gap, and a drafted
        action. Criterion 8 asks what was *done* about the gaps; a draft is a
        starting point for that sentence, never a substitute for it.
      * **Readiness** -- which courses have a mapping but no marks, or marks
        but no mapping, because a table is only as honest as its denominator.
    """
    from app.engine.office_export import (                  # noqa: PLC0415
        DocumentMeta, ExportUnavailable, tables_to_xlsx)
    from app.engine.sar_export import (                     # noqa: PLC0415
        Table, po_direct_attainment_table)

    data = po_attainment(admin, framework_code=framework_code, target=target)
    if not data["courses"]:
        raise HTTPException(
            status_code=400,
            detail="No course has both a submitted mapping and a submitted "
                   "attainment yet, so there is no PO attainment to export.")

    codes = [c["code"] for c in data["courses"]]
    t381 = po_direct_attainment_table(
        framework_code=framework_code, course_codes=codes,
        course_po_attainment={c["code"]: c["po"] for c in data["courses"]},
        direct_attainment=data["direct_attainment"])

    cqi = Table(
        number="CQI",
        title="Continuous Quality Improvement — actions on POs below target",
        headers=["PO", "Attained", "Target", "Gap", "Severity", "Drafted action"],
        rows=[[r["code"], f"{r['attained']:.2f}", f"{r['target']:.2f}",
               f"{r['gap']:.2f}", r["severity"], r["action"]]
              for r in data["cqi"]] or [["—", "—", "—", "—", "—",
                                         "Every PO is at or above target."]])

    rd = data["readiness"]
    readiness = Table(
        number="Basis",
        title="What this is computed from",
        headers=["Measure", "Courses"],
        rows=[["Subjects in the sheet", str(rd.get("courses", 0))],
              ["With a submitted CO–PO mapping", str(rd.get("mapped", 0))],
              ["With a submitted CO attainment", str(rd.get("measured", 0))],
              ["With both — the rows above", str(rd.get("both", 0))],
              ["Mapping but no marks yet", str(rd.get("mapping_only", 0))],
              ["Marks but no mapping yet", str(rd.get("marks_only", 0))]])

    years = sorted({c["academic_year"] for c in data["courses"] if c["academic_year"]})
    meta = DocumentMeta(program="B.Tech", framework=framework_code,
                        academic_year=", ".join(years),
                        note=("PO attainment is the submitted CO-PO mapping "
                              "multiplied by the submitted CO attainment; the "
                              "basis table below says how many courses that is."))
    try:
        blob = tables_to_xlsx([t381, cqi, readiness], meta=meta)
    except ExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    name = f"PO_Attainment_CQI_{'_'.join(years) or framework_code}.xlsx"
    return Response(
        content=blob,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/admin/po-coverage")
def po_coverage(admin: AdminDep, framework_code: str = "GAPC_V3") -> dict:
    """Every PO across every submitted mapping -- including the empty ones."""
    from app.engine.frameworks import get_framework            # noqa: PLC0415

    try:
        fw = get_framework(framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    got = neon.programme_po_coverage(list(fw.po_codes))
    got["po_titles"] = fw.po_titles
    got["framework_code"] = fw.code
    return got


# ----------------------------------------------------------------- teacher

@router.get("/my/courses")
def my_courses(user: UserDep) -> dict:
    """The signed-in teacher's own subjects, and nobody else's.

    An admin gets every course here too, because an admin who is also
    teaching a subject should not have to sign in twice.
    """
    if user.is_admin:
        return {"courses": neon.list_courses(), "role": user.role}
    return {"courses": neon.courses_for_teacher(user.id), "role": user.role}


@router.get("/my/submission/{course_id}")
def my_submission(course_id: int, user: UserDep) -> dict:
    if not user.is_admin and not neon.teacher_owns(course_id, user.id):
        raise HTTPException(status_code=403, detail="That is not your subject.")
    return {"submission": neon.get_submission(course_id)}


class OwnPassword(BaseModel):
    current: str
    new: str


@router.post("/me/password")
def change_password(req: OwnPassword, user: UserDep, response: Response) -> dict:
    """Change your own password, having proved you know the current one.

    Changing a password ends every session on the account, which is the whole
    point of it -- so a fresh one is issued here for the browser that just did
    it, or the person would be signed out by their own good hygiene.
    """
    try:
        ok = neon.change_own_password(user.id, req.current, req.new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not ok:
        raise HTTPException(status_code=403, detail="That is not your current password.")
    got = neon.login(user.username, req.new)
    if got:
        _set_cookie(response, got[0])
    neon.log(user.username, "change_own_password", {})
    return {"ok": True}


# ------------------------------------------------- internal CO attainment

class AttainmentRequest(BaseModel):
    course_id: int
    t1_pct: float = 45.0
    t2_pct: float = 60.0
    # {CO: max marks} and {CO: [one entry per student]}. Entries may be numbers
    # or the absence markers a teacher types -- AB, DB, blank.
    max_marks: dict[str, float] = Field(default_factory=dict)
    marks: dict[str, list] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)


def _attainment_from(req: AttainmentRequest):
    from app.engine.co_attainment import (                 # noqa: PLC0415
        AttainmentError, compute_co_attainment)
    try:
        return compute_co_attainment(
            req.marks, req.max_marks, t1_pct=req.t1_pct, t2_pct=req.t2_pct,
            students=len(req.names) or 0)
    except AttainmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/my/attainment/parse")
async def attainment_parse(user: UserDep, file: UploadFile = File(...)) -> dict:
    """Read a marks workbook and hand back the subject blocks in it.

    The department already keeps this file: one sheet per semester, five
    subjects side by side, a row of maximum marks and then the students. A
    teacher uploads it and picks their own block rather than retyping a
    hundred and seventy rows of marks.
    """
    from app.engine.co_attainment import (                 # noqa: PLC0415
        AttainmentError, read_marks_table)

    data = await file.read()
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400,
                            detail="Upload the marks sheet as .xlsx.")
    try:
        import openpyxl                                    # noqa: PLC0415
        import io                                          # noqa: PLC0415
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail=f"Could not read that workbook: {exc}") from None

    sheets = []
    for sheet in wb.sheetnames:
        rows = [[c.value for c in r] for r in wb[sheet].iter_rows()]
        try:
            blocks = read_marks_table(rows)
        except AttainmentError:
            continue        # a sheet that is not a marks table
        sheets.append({"sheet": sheet, "blocks": blocks})
    if not sheets:
        raise HTTPException(
            status_code=400,
            detail="No marks table found. The sheet needs a row of CO1, CO2 … "
                   "headers with the maximum marks under it and students below.")
    return {"sheets": sheets}


class PastedRows(BaseModel):
    rows: list[list] = Field(default_factory=list)


@router.post("/my/attainment/parse-text")
def attainment_parse_text(req: PastedRows, user: UserDep) -> dict:
    """The same reader, over marks pasted out of Excel rather than uploaded.

    One reader for both paths on purpose: two would drift, and the one that
    drifted would be the one nobody tested against the department's own file.
    """
    from app.engine.co_attainment import (                 # noqa: PLC0415
        AttainmentError, read_marks_table)
    try:
        return {"blocks": read_marks_table(req.rows)}
    except AttainmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/my/attainment/preview")
def attainment_preview(req: AttainmentRequest, user: UserDep) -> dict:
    """Compute without storing anything.

    The teacher sees the five summary rows their sheet has -- appeared, T1,
    crossing, percentage, attainment -- and the course average, and nothing is
    written until they press Submit.
    """
    if not user.is_admin and not neon.teacher_owns(req.course_id, user.id):
        raise HTTPException(status_code=403, detail="That is not your subject.")
    return {"attainment": _attainment_from(req).to_dict()}


@router.post("/my/attainment/submit")
def attainment_submit(req: AttainmentRequest, user: UserDep) -> dict:
    if not user.is_admin and not neon.teacher_owns(req.course_id, user.id):
        raise HTTPException(status_code=403, detail="That is not your subject.")
    got = _attainment_from(req)
    if not got.cos:
        raise HTTPException(status_code=400,
                            detail="No CO has marks to compute attainment from.")
    if all(c.appeared == 0 for c in got.cos):
        raise HTTPException(
            status_code=400,
            detail="No student has a mark, so there is nothing to submit.")
    payload = {**got.to_dict(), "names": req.names,
               "marks": req.marks, "max_marks": req.max_marks}
    row = neon.submit_attainment(
        course_id=req.course_id, user_id=user.id, t1_pct=req.t1_pct,
        t2_pct=req.t2_pct, internal=got.internal, students=got.students,
        payload=payload)
    neon.log(user.username, "submit_attainment",
             {"course_id": req.course_id, "internal": got.internal})
    return {"attainment": row, "computed": got.to_dict()}


@router.get("/my/attainment/{course_id}")
def attainment_get(course_id: int, user: UserDep) -> dict:
    if not user.is_admin and not neon.teacher_owns(course_id, user.id):
        raise HTTPException(status_code=403, detail="That is not your subject.")
    return {"attainment": neon.get_attainment(course_id)}


@router.get("/admin/attainment")
def attainment_all(admin: AdminDep) -> dict:
    """Every course's internal CO attainment, and the programme mean."""
    rows = neon.all_attainment()
    vals = [float(r["internal"]) for r in rows if r.get("internal") is not None]
    return {"attainment": rows,
            "programme_mean": round(sum(vals) / len(vals), 2) if vals else None}


@router.delete("/admin/attainment/{course_id}")
def attainment_delete(course_id: int, admin: AdminDep) -> dict:
    if not neon.delete_attainment(course_id):
        raise HTTPException(status_code=404, detail="Nothing submitted for that course.")
    neon.log(admin.username, "delete_attainment", {"course_id": course_id})
    return {"ok": True}


class SubmitRequest(BaseModel):
    course_id: int
    framework_code: str = "GAPC_V3"
    # {cos: [{code, statement, blooms_levels}], co_levels: {co: {po: level}}}
    payload: dict = Field(default_factory=dict)


@router.post("/my/submit")
def submit(req: SubmitRequest, user: UserDep) -> dict:
    """The only door into the database.

    Until this runs there is no row anywhere for this mapping -- which is the
    department's rule, stated as they stated it: if the teacher does not press
    Submit, the data is not stored.
    """
    if not user.is_admin and not neon.teacher_owns(req.course_id, user.id):
        raise HTTPException(status_code=403, detail="That is not your subject.")
    cos = req.payload.get("cos") or []
    levels = req.payload.get("co_levels") or {}
    if not cos:
        raise HTTPException(
            status_code=400,
            detail="There are no course outcomes to submit.")
    if not any(levels.get(c.get("code")) for c in cos):
        raise HTTPException(
            status_code=400,
            detail="No outcome is mapped to a PO yet, so there is nothing to submit.")
    row = neon.submit_mapping(course_id=req.course_id, user_id=user.id,
                              framework_code=req.framework_code,
                              payload=req.payload)
    neon.log(user.username, "submit", {"course_id": req.course_id,
                                       "cos": len(cos)})
    return {"submission": row, "course": neon.get_course(req.course_id)}
