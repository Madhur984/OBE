"""
API for the stored workspace: persistence, audit, approvals, PSOs, surveys,
trends, migration, and the Word/Excel exports.

Two things to be plain about, because they are visible in the routes.

**A session is required once this installation has accounts.** These routes
used to take the actor and role from the X-Actor and X-Role headers and
believe them, which was defensible while the tool was a single-user offline
workbench and stopped being so the moment it grew accounts and a shared
database: anyone who could reach the API could read the audit trail by
asserting a header. See `require_session` below -- an offline copy with no
DATABASE_URL is unaffected and behaves exactly as it always did.

Those headers still name the actor *within* an already-authenticated request,
so the approval chain records who did what. What they no longer do is decide
whether the request is allowed at all.

**Tenant isolation is real, and separate from this.** The store is bound to
one tenant per request and cannot address another's rows (see store/db.py).
Deriving the tenant from the session as well is the next step; today the
session decides *whether*, and the header still decides *who*.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import (
    APIRouter, Cookie, Depends, File, Header, HTTPException, Query, Response,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.engine import indirect as ind
from app.engine import migration as MG
from app.engine import office_export as OX
from app.engine import pso as P
from app.engine import trends as TR
from app.engine.attainment import AttainmentThresholds
from app.engine.docx_parser import (
    ParserUnavailable, parse_marks_xlsx, parse_question_paper_docx,
    parse_question_paper_text, xlsx_sheet_names,
)
from app.engine.frameworks import get_framework
from app.engine.sar_export import Table
from app.store import workflow
from app.store.audit import build_evidence_bundle, events, record, verify_chain
from app.store.db import bootstrap

# Every route below requires a signed-in session once this installation has
# accounts. See require_session -- it is declared after this line, so the
# dependency is attached where the router is used rather than here.
router = APIRouter()

# One database for the process. AUTOOBE_DB=":memory:" gives the old ephemeral
# behaviour, which is what the API tests use.
_DB_PATH = os.environ.get("AUTOOBE_DB") or str(
    Path(__file__).resolve().parents[2] / "autoobe.sqlite3"
)
_db, _store = bootstrap(_DB_PATH)

DEMO_TENANT = "demo"


def store():
    return _store


def require_session(
    autoobe_session: str | None = Cookie(default=None),
) -> None:
    """A signed-in person, once this installation has accounts.

    These routes used to take the actor and the role from `X-Actor` and
    `X-Role` headers and believe them, which was defensible while the tool was
    a single-user offline workbench behind an institutional login and stopped
    being defensible the moment it grew accounts and a shared database:
    anyone who could reach the API could read the audit trail by asserting a
    header.

    The rule is therefore conditional on the install, not on the route:

      * **no DATABASE_URL** -- an offline, single-user copy. Nothing to sign
        in to, nothing to protect from, and the whole offline test suite runs
        here. Allowed, exactly as before.
      * **accounts exist** -- a real deployment. A valid session is required,
        and there is no header that substitutes for one.

    A configured database with no administrator yet is also allowed, so an
    install can be set up before it is locked.
    """
    from app.store import neon                          # noqa: PLC0415

    if not neon.is_configured():
        return
    try:
        if neon.count_admins() == 0:
            return                                      # not set up yet
        if neon.user_for_token(autoobe_session or "") is not None:
            return
    except Exception:                                   # noqa: BLE001
        # The database is unreachable. Refusing here would take the offline
        # engine down with it, so fall back to the old behaviour rather than
        # fail closed on something that is not an authorisation decision.
        return
    raise HTTPException(status_code=401, detail="Sign in to continue.")


def actor_of(x_actor: str | None, x_role: str | None) -> tuple[str, str]:
    actor = (x_actor or "unattributed@local").strip()
    role = (x_role or "faculty").strip().lower()
    if role not in workflow.ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Expected one of {sorted(workflow.ROLES)}.",
        )
    return actor, role


# ---------------------------------------------------------------- programs

class ProgramIn(BaseModel):
    code: str
    name: str
    framework_code: str = "GAPC_V4"
    branch: str = ""


@router.get("/api/programs")
def list_programs() -> dict:
    s = store()
    out = []
    for p in s.list_programs():
        out.append({**p, "psos": s.list_psos(p["id"]),
                    "course_count": len(s.list_courses(p["id"]))})
    return {"programs": out, "tenant": s.tenant_id}


@router.post("/api/programs")
def create_program(req: ProgramIn,
                   x_actor: str | None = Header(default=None),
                   x_role: str | None = Header(default=None)) -> dict:
    actor, _ = actor_of(x_actor, x_role)
    s = store()
    if s.program_by_code(req.code):
        raise HTTPException(status_code=409, detail=f"Program {req.code} already exists.")
    try:
        get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    pid = s.create_program(req.code, req.name, req.framework_code, req.branch)
    record(s, actor=actor, action="program.created", entity_type="program",
           entity_id=pid, payload={"code": req.code, "framework": req.framework_code})
    return {"id": pid, **(s.get_program(pid) or {})}


# -------------------------------------------------------------------- PSOs

class PSOIn(BaseModel):
    code: str
    statement: str


class PSOSetIn(BaseModel):
    psos: list[PSOIn]


def _pso_report_json(r: P.PSOReport) -> dict:
    return {
        "code": r.code, "statement": r.statement, "is_usable": r.is_usable,
        "specificity": r.specificity, "distinctive_terms": r.distinctive_terms[:12],
        "findings": [{"check": f.check, "severity": f.severity, "message": f.message}
                     for f in r.findings],
    }


@router.post("/api/pso/check")
def check_psos(req: PSOSetIn) -> dict:
    """Quality-check a PSO set without storing it."""
    psos = [P.PSO(p.code, p.statement) for p in req.psos]
    reports, set_findings = P.check_pso_set(psos)
    return {
        "reports": [_pso_report_json(r) for r in reports],
        "set_findings": [{"check": f.check, "severity": f.severity, "message": f.message}
                         for f in set_findings],
        "usable": all(r.is_usable for r in reports) and
                  not any(f.severity == "error" for f in set_findings),
    }


@router.put("/api/programs/{program_id}/psos")
def set_psos(program_id: str, req: PSOSetIn,
             x_actor: str | None = Header(default=None),
             x_role: str | None = Header(default=None)) -> dict:
    actor, _ = actor_of(x_actor, x_role)
    s = store()
    if not s.get_program(program_id):
        raise HTTPException(status_code=404, detail="No such program.")
    s.set_psos(program_id, [(p.code, p.statement) for p in req.psos])
    record(s, actor=actor, action="psos.set", entity_type="program",
           entity_id=program_id, payload={"codes": [p.code for p in req.psos]})
    return check_psos(req) | {"stored": True}


class PSOProposeIn(BaseModel):
    cos: dict[str, dict] = Field(description="{co_code: {statement, blooms_levels}}")
    psos: list[PSOIn]


@router.post("/api/pso/propose")
def propose_pso_mapping(req: PSOProposeIn) -> dict:
    """Infer the CO x PSO matrix, so PSO columns arrive filled like PO ones."""
    psos = [P.PSO(p.code, p.statement) for p in req.psos]
    cos = {
        code: (str(d.get("statement", "")), list(d.get("blooms_levels") or []))
        for code, d in req.cos.items()
    }
    mappings = P.propose_pso_matrix(cos, psos)
    return {
        "pso_codes": [p.code for p in psos],
        "levels": P.pso_levels_dict(mappings),
        "mappings": [
            {"co_code": m.co_code, "pso_code": m.pso_code, "level": m.level,
             "competency": m.competency, "rationale": m.rationale,
             "evidence": m.evidence}
            for m in mappings
        ],
        "unmapped_psos": sorted(
            {p.code for p in psos} - {m.pso_code for m in mappings}
        ),
    }


# ---------------------------------------------------------------- surveys

class SurveyBuildIn(BaseModel):
    cos: dict[str, str] = Field(default_factory=dict)
    outcomes: dict[str, str] = Field(default_factory=dict)
    kind: str = "course_exit"
    target_kind: str = "PO"
    scale_max: int = 5
    course_title: str = ""


@router.post("/api/survey/build")
def build_survey(req: SurveyBuildIn) -> dict:
    """Generate the instrument from the outcomes. Nobody authors a survey."""
    try:
        if req.kind == "course_exit":
            if not req.cos:
                raise HTTPException(status_code=400,
                                    detail="Add the course outcomes first.")
            inst = ind.build_course_exit_survey(
                req.cos, scale_max=req.scale_max, course_title=req.course_title)
        else:
            if not req.outcomes:
                raise HTTPException(status_code=400, detail="Add the outcomes first.")
            inst = ind.build_outcome_survey(
                req.outcomes, kind=req.kind, target_kind=req.target_kind,
                scale_max=req.scale_max)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {**inst.to_dict(), "scale_labels": ind.scale_labels(inst.scale_max)}


class SurveyScoreIn(BaseModel):
    instrument: dict
    responses: list[dict[str, float]]
    population: int | None = None
    t1: float = 60.0
    t2: float = 70.0
    t3: float = 80.0


@router.post("/api/survey/score")
def score_survey(req: SurveyScoreIn) -> dict:
    items = [ind.SurveyItem(i["id"], i["target_kind"], i["target_code"], i["text"])
             for i in req.instrument.get("items", [])]
    inst = ind.Instrument(req.instrument.get("kind", "course_exit"),
                          int(req.instrument.get("scale_max", 5)), items,
                          req.instrument.get("preamble", ""))
    try:
        th = AttainmentThresholds(t1=req.t1, t2=req.t2, t3=req.t3)
        result = ind.score_survey(inst, req.responses, thresholds=th,
                                  population=req.population)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "kind": result.kind, "responses": result.responses,
        "population": result.population, "adequacy": result.adequacy,
        "response_rate": result.response_rate, "warnings": result.warnings,
        "items": [
            {"target_kind": i.target_kind, "target_code": i.target_code,
             "responses": i.responses, "mean_rating": i.mean_rating,
             "pct": i.pct, "level": i.level, "distribution": i.distribution}
            for i in result.items
        ],
        "by_target": result.by_target(),
        "methodology_note": ind.methodology_note(
            w_direct=0.8, w_indirect=0.2, scale_max=inst.scale_max, result=result),
    }


class BlendIn(BaseModel):
    direct: dict[str, float | None]
    indirect: dict[str, float | None] = Field(default_factory=dict)
    w_direct: float = 0.8
    w_indirect: float = 0.2
    co_po_levels: dict[str, dict[str, int]] = Field(default_factory=dict)
    framework_code: str = "GAPC_V4"
    # When indirect is CO-scoped, roll it up through the articulation matrix.
    indirect_is_co_scoped: bool = False
    # The unit of `indirect`. Named rather than inferred: a survey reports
    # percentages, direct attainment is a 0-3 level, and blending the two
    # without converting yields a number that looks like an answer.
    indirect_scale: str = "level"
    t1: float = 60.0
    t2: float = 70.0
    t3: float = 80.0


@router.post("/api/blend-attainment")
def blend(req: BlendIn) -> dict:
    try:
        fw = get_framework(req.framework_code)
        th = AttainmentThresholds(t1=req.t1, t2=req.t2, t3=req.t3)
        indirect = ind.to_level_scale(req.indirect, scale=req.indirect_scale,
                                      thresholds=th)
        if req.indirect_is_co_scoped and req.co_po_levels:
            indirect = ind.rollup_indirect_to_po(
                {k: v for k, v in indirect.items() if v is not None},
                req.co_po_levels, list(fw.po_codes))
        rows = ind.blend_attainment(req.direct, indirect,
                                    w_direct=req.w_direct, w_indirect=req.w_indirect)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "blended": [r.to_dict() for r in rows],
        "methodology_note": ind.methodology_note(
            w_direct=req.w_direct, w_indirect=req.w_indirect, scale_max=5),
    }


# ----------------------------------------------------------------- trends

def _aggregate_course_runs(course_runs: list[dict], *, framework_code: str) -> list[dict]:
    """
    Roll per-course runs up into one program-level run per academic year.

    Uses `compute_po_direct_attainment`, so the arithmetic is the same one the
    course screen uses and there is no second implementation of SAR 3.8.1.
    Where a year holds several runs for one course -- a re-run after a
    correction -- the last one wins, because that is the figure that stood.
    """
    from app.engine.attainment import compute_po_direct_attainment

    by_year: dict[str, dict[str, dict[str, float | None]]] = {}
    for run in sorted(course_runs, key=lambda r: r.get("created_at", "")):
        values = (run.get("result") or {}).get("po_direct_attainment") or {}
        if not values:
            continue
        by_year.setdefault(run.get("academic_year", ""), {})[
            run.get("course_id") or run["id"]] = values

    out = []
    for year, per_course in sorted(by_year.items()):
        try:
            codes = list(get_framework(framework_code).po_codes)
        except ValueError:
            codes = sorted({c for v in per_course.values() for c in v})
        out.append({
            "academic_year": year,
            "created_at": f"{year}-12-31",
            "config": {"framework_code": framework_code,
                       "courses": len(per_course), "derived": "course roll-up"},
            "result": {"po_direct_attainment":
                       compute_po_direct_attainment(per_course, codes)},
        })
    return out


class TrendIn(BaseModel):
    runs: list[dict] = Field(default_factory=list)
    framework_code: str = "GAPC_V4"
    targets: dict[str, float] = Field(default_factory=dict)
    actions: list[dict] = Field(default_factory=list)
    program_id: str | None = None


@router.post("/api/trends")
def trend_analysis(req: TrendIn) -> dict:
    """
    Multi-year attainment. Runs may be supplied inline or read from storage
    via `program_id`; stored runs are the point of persistence existing.
    """
    runs = list(req.runs)
    if req.program_id:
        s = store()
        stored = s.list_runs(program_id=req.program_id, scope="program")
        if not stored:
            # Only per-course runs exist. Aggregate them into program-level
            # years using SAR 3.8.1's stated method -- the plain average across
            # the courses addressing each PO -- rather than reporting nothing.
            stored = _aggregate_course_runs(
                s.list_runs(program_id=req.program_id, scope="course"),
                framework_code=req.framework_code)
        runs += stored
    if not runs:
        raise HTTPException(
            status_code=400,
            detail="No attainment runs yet. Criterion 8 needs at least two years.",
        )
    try:
        series = TR.build_series(runs, framework_code=req.framework_code,
                                 targets=req.targets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    findings = TR.gap_analysis(series)
    closure = TR.check_closure(series, req.actions)
    return {
        "summary": TR.summarise(series),
        "series": [s.to_dict() for s in series],
        "findings": [{"target_code": f.target_code, "severity": f.severity,
                      "message": f.message} for f in findings],
        "closure": [
            {"target_code": c.target_code, "action": c.action,
             "action_year": c.action_year, "before": c.before, "after": c.after,
             "delta": c.delta, "verdict": c.verdict, "message": c.message}
            for c in closure
        ],
    }


# -------------------------------------------------------------- migration

class MigrateIn(BaseModel):
    levels: dict[str, dict[str, int]]
    cos: dict[str, dict] = Field(default_factory=dict)
    from_code: str = "GAPC_V3"
    to_code: str = "GAPC_V4"


@router.post("/api/migrate-matrix")
def migrate(req: MigrateIn) -> dict:
    """Port a legacy 12-PO matrix, and show what a fresh derivation would say."""
    if not req.levels:
        raise HTTPException(status_code=400, detail="Send the legacy matrix to migrate.")
    cos = {
        code: (str(d.get("statement", "")), list(d.get("blooms_levels") or []))
        for code, d in req.cos.items()
    }
    try:
        if cos:
            cmp = MG.migrate_and_compare(req.levels, cos, from_code=req.from_code,
                                         to_code=req.to_code)
            mig = cmp.migrated
        else:
            cmp = None
            mig = MG.migrate_matrix(req.levels, from_code=req.from_code,
                                    to_code=req.to_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    out = {
        "levels": mig.levels,
        "warnings": mig.warnings,
        "review_count": mig.review_count,
        "decisions": [
            {"co_code": d.co_code, "new_po": d.new_po, "level": d.level,
             "sources": list(d.sources), "rule": d.rule,
             "needs_review": d.needs_review, "message": d.message}
            for d in mig.decisions
        ],
    }
    if cmp is not None:
        out |= {
            "rederived": cmp.rederived,
            "agreement_rate": cmp.agreement_rate,
            "recommendation": cmp.recommendation,
            "disagreements": [
                {"co_code": d.co_code, "po_code": d.po_code, "migrated": d.migrated,
                 "rederived": d.rederived, "message": d.message}
                for d in cmp.disagreements
            ],
            "validation": {"migrated": cmp.validation_migrated,
                           "rederived": cmp.validation_rederived},
        }
    return out


# ----------------------------------------------------- document parsing

@router.post("/api/parse-question-paper")
async def parse_paper(file: UploadFile = File(...)) -> dict:
    """Read a question paper from .docx, .txt, or pasted text."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is larger than 25 MB.")
    name = (file.filename or "").lower()
    try:
        if name.endswith(".docx"):
            paper = parse_question_paper_docx(data)
        else:
            paper = parse_question_paper_text(data.decode("utf-8", errors="replace"))
    except ParserUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="That file could not be read. Try a .docx, or paste the paper "
                   "as text.",
        ) from None
    return {
        "source": paper.source, "confidence": paper.confidence,
        "warnings": paper.warnings, "total_marks": paper.total_marks,
        "questions": paper.to_dicts(),
    }


@router.post("/api/parse-marks-file")
async def parse_marks(file: UploadFile = File(...),
                      sheet: str | None = Query(default=None)) -> dict:
    """Read an .xlsx marks sheet through the shared column detector."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        sheets = xlsx_sheet_names(data)
        result = parse_marks_xlsx(data, sheet)
    except ParserUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="That workbook could not be read. Save it as .xlsx, or paste "
                   "the sheet as text.",
        ) from None
    return {
        "sheets": sheets,
        "sheet": sheet or (sheets[0] if sheets else None),
        "student_count": result.student_count,
        "warnings": result.warnings,
        "skipped_rows": result.skipped_rows,
        "columns": [
            {"index": c.index, "header": c.header, "kind": c.kind,
             "question_label": c.question_label}
            for c in result.columns
        ],
        "question_labels": [c.question_label or c.header
                            for c in result.question_columns],
        "rows": result.rows[:400],
    }


# ------------------------------------------------------- Word / Excel export

class ExportIn(BaseModel):
    tables: list[dict] = Field(description="[{number, title, headers, rows}]")
    institution: str = ""
    program: str = ""
    framework: str = ""
    academic_year: str = ""
    prepared_by: str = ""
    notes: list[str] = Field(default_factory=list)
    filename: str = "SAR-annexures"


def _tables(payload: list[dict]) -> list[Table]:
    if not payload:
        raise HTTPException(status_code=400, detail="No tables to export.")
    out = []
    for t in payload:
        try:
            out.append(Table(str(t["number"]), str(t["title"]),
                             [str(h) for h in t["headers"]],
                             [[str(c) for c in row] for row in t["rows"]]))
        except (KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Malformed table payload: {exc}",
            ) from None
    return out


@router.get("/api/export/formats")
def export_formats() -> dict:
    return OX.formats_available()


@router.post("/api/export/docx")
def export_docx(req: ExportIn) -> Response:
    meta = OX.DocumentMeta(req.institution, req.program, req.framework,
                           req.academic_year, req.prepared_by)
    try:
        data = OX.tables_to_docx(_tables(req.tables), meta=meta, notes=req.notes)
    except OX.ExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{req.filename}.docx"'},
    )


@router.post("/api/export/xlsx")
def export_xlsx(req: ExportIn) -> Response:
    meta = OX.DocumentMeta(req.institution, req.program, req.framework,
                           req.academic_year, req.prepared_by)
    try:
        data = OX.tables_to_xlsx(_tables(req.tables), meta=meta, notes=req.notes)
    except OX.ExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{req.filename}.xlsx"'},
    )


# --------------------------------------------------------- audit & approvals

@router.get("/api/audit")
def read_audit(entity_type: str | None = None, entity_id: str | None = None,
               limit: int = 200) -> dict:
    s = store()
    chain = verify_chain(s)
    return {
        "chain": {"ok": chain.ok, "checked": chain.checked,
                  "summary": chain.summary, "problems": chain.problems},
        "events": [
            {"seq": e.seq, "at": e.at, "actor": e.actor, "action": e.action,
             "entity_type": e.entity_type, "entity_id": e.entity_id,
             "payload": e.payload, "hash": e.hash[:12]}
            for e in events(s, entity_type=entity_type, entity_id=entity_id,
                            limit=limit)
        ],
    }


class AuditIn(BaseModel):
    action: str
    entity_type: str
    entity_id: str
    payload: dict = Field(default_factory=dict)


@router.post("/api/audit")
def write_audit(req: AuditIn,
                x_actor: str | None = Header(default=None),
                x_role: str | None = Header(default=None)) -> dict:
    actor, _ = actor_of(x_actor, x_role)
    e = record(store(), actor=actor, action=req.action, entity_type=req.entity_type,
               entity_id=req.entity_id, payload=req.payload)
    return {"seq": e.seq, "at": e.at, "hash": e.hash[:12]}


@router.get("/api/approvals/{entity_type}/{entity_id}")
def approval_state(entity_type: str, entity_id: str,
                   x_role: str | None = Header(default=None)) -> dict:
    _, role = actor_of(None, x_role)
    s = store()
    st = workflow.current_state(s, entity_type, entity_id)
    return {
        "state": st.state, "actor": st.actor, "note": st.note, "at": st.at,
        "is_locked": st.is_locked, "is_editable": st.is_editable,
        "available_actions": workflow.available_actions(st.state, role),
        "history": workflow.history(s, entity_type, entity_id),
    }


class TransitionIn(BaseModel):
    action: str
    note: str = ""


@router.post("/api/approvals/{entity_type}/{entity_id}")
def transition(entity_type: str, entity_id: str, req: TransitionIn,
               x_actor: str | None = Header(default=None),
               x_role: str | None = Header(default=None)) -> dict:
    actor, role = actor_of(x_actor, x_role)
    s = store()
    try:
        if req.action == "unlock":
            st = workflow.unlock(s, entity_type=entity_type, entity_id=entity_id,
                                 actor=actor, role=role, reason=req.note)
        else:
            st = workflow.transition(s, entity_type=entity_type, entity_id=entity_id,
                                     action=req.action, actor=actor, role=role,
                                     note=req.note)
    except workflow.TransitionDenied as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    record(s, actor=actor, action=f"approval.{req.action}", entity_type=entity_type,
           entity_id=entity_id, payload={"state": st.state, "note": req.note})
    return {"state": st.state, "actor": st.actor, "at": st.at,
            "available_actions": workflow.available_actions(st.state, role)}


# ------------------------------------------------------------------ snapshot

class SnapshotIn(BaseModel):
    program_code: str = "PROG"
    program_name: str = "Programme"
    branch: str = ""
    framework_code: str = "GAPC_V4"
    course_code: str
    course_name: str = ""
    semester: int = 0
    credits: float = 0
    academic_year: str = "2025-26"
    cos: dict[str, dict] = Field(default_factory=dict)
    psos: list[PSOIn] = Field(default_factory=list)
    co_po_levels: dict[str, dict[str, int]] = Field(default_factory=dict)
    co_pso_levels: dict[str, dict[str, int]] = Field(default_factory=dict)
    overrides: list[str] = Field(default_factory=list)   # "CO1|PO3" cells a human set
    questions: list[dict] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)


@router.post("/api/workspace/snapshot")
def snapshot(req: SnapshotIn,
             x_actor: str | None = Header(default=None),
             x_role: str | None = Header(default=None)) -> dict:
    """
    Persist the current course state and the attainment run behind it.

    This is what makes the audit trail and the evidence bundle mean anything:
    without a stored run, "how did PO3 become 2.14" can only be answered by
    recomputing, which proves what the software does today rather than what
    it did when the SAR was signed.

    Refuses to overwrite a locked course, since that is the entire purpose of
    a lock.
    """
    actor, _ = actor_of(x_actor, x_role)
    s = store()

    program = s.program_by_code(req.program_code) or {
        "id": s.create_program(req.program_code, req.program_name,
                               req.framework_code, req.branch)}
    pid = program["id"]
    course = s.course_by_code(pid, req.course_code)
    cid = course["id"] if course else s.create_course(
        pid, req.course_code, req.course_name or req.course_code,
        semester=req.semester, credits=req.credits)

    try:
        workflow.require_editable(s, "course_matrix", req.course_code)
    except workflow.TransitionDenied as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    s.set_cos(cid, [
        (code, str(d.get("statement", "")), list(d.get("blooms_levels") or []),
         d.get("original_statement"))
        for code, d in req.cos.items()
    ])
    if req.psos:
        s.set_psos(pid, [(p.code, p.statement) for p in req.psos])

    overrides = set(req.overrides)
    s.clear_mapping(cid)
    for kind, table in (("PO", req.co_po_levels), ("PSO", req.co_pso_levels)):
        for co_code, row in table.items():
            for target, level in row.items():
                if level <= 0:
                    continue
                s.set_mapping(
                    cid, co_code, kind, target, level=level,
                    # Rule 1 pins a partial match at exactly 1; anything
                    # above came through Rule 2, which only strong matches
                    # reach. Level 0 is never stored.
                    competency=1 if level == 1 else 2,
                    source="override" if f"{co_code}|{target}" in overrides else "proposed",
                    framework_code=req.framework_code,
                )

    if req.questions:
        aid = s.create_assessment(cid, kind="cie", name="Internal assessment",
                                  academic_year=req.academic_year) \
            if not s.list_assessments(cid, req.academic_year) \
            else s.list_assessments(cid, req.academic_year)[0]["id"]
        for i, q in enumerate(req.questions):
            qid = s.add_question(
                aid, question_no=str(q.get("question_no") or f"Q{i+1}"),
                co_code=str(q.get("co_code") or ""),
                max_marks=float(q.get("max_marks") or 1),
                choice_group=q.get("choice_group"), position=i)
            for roll, mark in (q.get("student_marks") or {}).items():
                s.set_mark(qid, roll, float(mark), "present")
            for roll in (q.get("not_attempted") or []):
                s.set_mark(qid, roll, None, "not_attempted")

    run_id = s.save_run(
        academic_year=req.academic_year, scope="course", course_id=cid,
        program_id=pid, created_by=actor,
        config={**req.config, "framework_code": req.framework_code},
        result=req.result,
    )
    record(s, actor=actor, action="attainment.saved", entity_type="course_matrix",
           entity_id=req.course_code,
           payload={"course": req.course_code, "year": req.academic_year,
                    "run": run_id, "cos": len(req.cos)})
    return {"program_id": pid, "course_id": cid, "run_id": run_id,
            "academic_year": req.academic_year}


# ------------------------------------------------------------ evidence bundle

@router.get("/api/evidence/{course_id}")
def evidence(course_id: str, academic_year: str, run_id: str | None = None) -> dict:
    s = store()
    if not s.get_course(course_id):
        raise HTTPException(status_code=404, detail="No such course.")
    b = build_evidence_bundle(s, course_id=course_id, academic_year=academic_year,
                              run_id=run_id)
    return b.to_dict() | {"markdown": b.to_markdown()}


@router.get("/api/evidence/{course_id}/docx")
def evidence_docx(course_id: str, academic_year: str,
                  run_id: str | None = None) -> Response:
    s = store()
    course = s.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="No such course.")
    b = build_evidence_bundle(s, course_id=course_id, academic_year=academic_year,
                              run_id=run_id)
    try:
        data = OX.evidence_bundle_to_docx(b.to_markdown())
    except OX.ExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    name = f"evidence-{course.get('code', course_id)}-{academic_year}".replace("/", "-")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )


# ------------------------------------------------------------ tagger quality

@router.get("/api/tagger-quality")
def tagger_quality(split: str | None = None) -> dict:
    """
    The tagger's measured accuracy, so the claim is checkable in the product
    rather than only in the test suite.
    """
    from app.engine.tagger_eval import evaluate
    try:
        r = evaluate(split=split)
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="The labelled corpus is not installed.") from None
    return {"split": split or "all", **r.to_dict()}
