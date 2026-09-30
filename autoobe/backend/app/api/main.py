"""
FastAPI layer over the deterministic engine.

The engine stays the single source of truth for every number: this module only
validates input, calls pure functions, and serialises results. No arithmetic
lives here, and none is duplicated in the browser -- the UI renders what the
engine computed (plan Section 8: numbers that can reach a SAR come from one
deterministic path).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.accounts import require_admin, router as accounts_router
from app.api.workspace import require_session, router as workspace_router
from app.engine.attainment import (
    AttainmentThresholds, AttemptPolicy, QuestionMarks, TargetMode, blend_direct,
    choice_bias_report, compute_co_attainment_cie, compute_co_attainment_see,
    compute_po_attainment_for_course, compute_po_direct_attainment,
)
from app.engine.aktu_catalogue import find_course, load_catalogue, syllabus_for
from app.engine.co_quality import check_co
from app.engine.co_rewriter import rewrite_all, rewrite_co
from app.engine.competency import propose_for_co, propose_matrix, signal_strength
from app.engine.constraints import (
    po_course_coverage, validate_course_matrix, validate_program_matrix,
)
from app.engine.conventions import (
    COURSE_KIND_LABEL, CORE_COLUMNS, ConventionConfig, CoreConfig,
    apply_convention, apply_core_columns, classify_course, columns_for_course,
    withhold_core_columns,
)
from app.engine.copo_document import (
    METHOD_NOTE, build_copo_document, convention_note, course_block_table, po_averages,
)
from app.engine.cqi import generate_co_cqi, generate_po_cqi, summarise
from app.engine.frameworks import (
    FRAMEWORKS, UI_DEFAULT_FRAMEWORK, get_framework, lifelong_po_code,
    migrate_po_code,
)
from app.engine.ingest import detect_outliers, ingest_marks, to_question_payload
from app.engine.office_export import (
    DocumentMeta, ExportUnavailable, course_blocks_to_xlsx, tables_to_docx,
    tables_to_xlsx,
)
from app.engine.mapping_rules import Competency, map_co_to_po, parse_competency
from app.engine.pso import pso_action_level
from app.engine.qco_tagger import (
    llm_status, split_question_paper, tag_paper, tag_paper_smart,
)
from app.engine.syllabus_parser import infer_missing_bloom_levels, parse_text, parse_upload
from app.engine.verb_advisor import (
    ai_recommendation, apply_verb, detect_issue, detect_vague_opening,
    detect_verbless_opening, level_change, offline_recommendation,
    suggest_verbs,
)
from app.engine.sar_export import (
    Table, course_articulation_matrix, po_direct_attainment_table,
)
from app.seed import curriculum


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Open the database connection while the server starts, not while a
    teacher waits.

    Neon scales its compute to zero, so the first query after an idle spell
    pays a cold start of several seconds. Paid here, once, on a background
    thread, it is not paid by whoever happens to open the sign-in page first
    -- which is how a perfectly healthy install came to show "The database is
    not connected".

    Best-effort on purpose: an offline install has no DATABASE_URL and must
    still start, so every failure is swallowed and the mapping engine runs
    exactly as it does with no database at all. `/api/auth/status` reports the
    real state, and the sign-in page retries.
    """
    import threading                                        # noqa: PLC0415

    from app.store import neon                              # noqa: PLC0415

    if neon.is_configured():
        def warm() -> None:
            try:
                neon.ensure_schema()
            except Exception:                               # noqa: BLE001
                pass
        threading.Thread(target=warm, name="neon-warmup", daemon=True).start()
    yield


app = FastAPI(
    title="AutoOBE",
    description="CO-PO mapping and NBA attainment. Deterministic core, no AI in this path.",
    version="0.1.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"

# Persistence, audit, approvals, PSOs, surveys, trends, migration, exports.
#
# Guarded by a session once this installation has accounts. These routes used
# to take the actor and role from request headers and trust them, which meant
# anyone who could reach the API could read the audit trail by asserting one.
# An offline copy with no DATABASE_URL is unaffected -- see require_session.
app.include_router(workspace_router, dependencies=[Depends(require_session)])
# Accounts, course assignment and submitted mappings. Dormant without a
# DATABASE_URL: every route in it answers 503 with a sentence saying so, and
# the mapping engine below runs exactly as it does offline.
app.include_router(accounts_router)


# ---------------------------------------------------------------- frameworks

@app.get("/api/frameworks")
def list_frameworks() -> dict:
    return {
        "frameworks": [
            {
                "code": fw.code,
                "name": fw.name,
                "po_count": fw.po_count,
                "po_codes": list(fw.po_codes),
                "po_titles": fw.po_titles,
                "po_statements": fw.po_statements,
                "po_action_levels": fw.po_action_levels,
                "po_wk_refs": {k: list(v) for k, v in fw.po_wk_refs.items()},
                "wk_statements": fw.wk_statements,
            }
            for fw in FRAMEWORKS.values()
        ],
        "default": "GAPC_V4",
        # What the "/" page opens on, which is a different decision: colleges
        # are still filing against the 12-PO v3 framework, and their live
        # mapping sheets are PO1..PO12. The engine default above stays v4
        # because the evaluation corpus is labelled against it.
        "ui_default": UI_DEFAULT_FRAMEWORK,
    }


# ------------------------------------------------------- the AKTU catalogue

@app.get("/api/catalogue")
def catalogue_lookup(title: str = "", code: str = "", limit: int = 5) -> dict:
    """
    What AKTU publishes for this course.

    The department was asked to upload five more syllabus PDFs to close the
    gaps on their non-technical blocks. This is the answer to that: the
    university's own syllabi ship with the tool, and a course is found by the
    code and title already in the workbook.

    The match is returned with its score and its source document rather than
    applied silently, because standing a 2021 syllabus in for a 2026 course is
    a judgement the department may want to refuse.
    """
    cat = load_catalogue()
    best, score = find_course(title=title, code=code, catalogue=cat)
    from app.engine.aktu_catalogue import score as _score      # noqa: PLC0415
    near = sorted(cat, key=lambda c: -_score(title, code, c))[:max(0, limit)]
    return {
        "courses": len(cat),
        "match": best.to_dict() if best else None,
        "score": score,
        # The runners-up, so a wrong match can be corrected rather than argued
        # with. Empty when nothing was asked for.
        "near": [{**c.to_dict(), "score": round(_score(title, code, c), 2)}
                 for c in near if (title or code)],
    }


@app.get("/api/crosswalk/{po_code}")
def crosswalk(po_code: str, from_code: str = "GAPC_V3", to_code: str = "GAPC_V4") -> dict:
    try:
        targets = migrate_po_code(po_code.upper(), from_code=from_code, to_code=to_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"po_code": po_code.upper(), "from": from_code, "to": to_code,
            "maps_to": list(targets)}


# ------------------------------------------------------------- CO quality

class COCheckRequest(BaseModel):
    co_code: str = Field(default="CO1")
    statement: str
    blooms_levels: list[int] = Field(default_factory=list)


@app.post("/api/co-quality")
def co_quality(req: COCheckRequest) -> dict:
    r = check_co(req.co_code, req.statement, declared_blooms_levels=req.blooms_levels or None)
    return {
        "co_code": r.co_code,
        "is_usable": r.is_usable,
        "detected_verb": r.detected_verb,
        "detected_bloom_level": r.detected_bloom_level,
        "findings": [
            {"check": f.check, "severity": f.severity.value, "message": f.message}
            for f in r.findings
        ],
    }


# ---------------------------------------------------------------- mapping

class MappingRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    co_blooms: dict[str, list[int]]
    # {co_code: {po_code: 0 | 1 | 2}}. Legacy "none"/"partial"/"strong" strings
    # are still accepted so stored matrices keep working.
    competency: dict[str, dict[str, int | str]]
    # The department's own PSOs, {code: statement}. A PSO cell is levelled by
    # the same Rule 2 as a PO; its PL comes from the leading verb of the
    # statement the department wrote, so a PSO with no statement has no
    # column and no level.
    psos: dict[str, str] = Field(default_factory=dict)
    # Apply the departmental life-long learning convention to COs the engine
    # left unmapped against that PO. Off unless asked for -- see
    # app/engine/conventions.py.
    lifelong_convention: bool = False
    lifelong_competency: int = 2
    # The columns the department asserts for every outcome of a technical
    # course. Presence only -- Rule 2 still decides each level. Off unless
    # asked for; see app/engine/conventions.py.
    core_convention: bool = False
    core_codes: list[str] = Field(default_factory=lambda: list(CORE_COLUMNS))
    # "keep" leaves a weaker reading the engine made; "raise" lets the
    # department's assertion win. Every raised cell is reported.
    core_on_conflict: str = "keep"
    # Fill a core column only where the outcome, or the course syllabus behind
    # it, shows some trace of that PO. Needs `co_statements`; without them
    # there is nothing to weigh and the convention fills the column as before.
    core_require_evidence: bool = False
    # What an unevidenced core column gets on a technical course: "blank"
    # leaves it out, "partial" records it at 1. See CoreConfig.unevidenced.
    core_unevidenced: str = "blank"
    # {co_code: statement} and the course's unit-wise syllabus, used only to
    # decide which columns have any evidence. Nothing here changes a proposal.
    co_statements: dict[str, str] = Field(default_factory=dict)
    syllabus: str = ""
    # What kind of course this is, so the core columns are the ones a course
    # of that kind actually contributes to. Given these, the columns are
    # derived and `core_codes` is ignored -- a lab, a project and a course on
    # the Constitution of India do not share a column set, and applying one
    # set to a whole workbook is what puts engineering knowledge against rural
    # development. Leave them blank and `core_codes` is used verbatim.
    course_title: str = ""
    course_code: str = ""
    course_ltp: list[int] = Field(default_factory=list)
    # When no syllabus was uploaded, stand in the one AKTU publishes for this
    # course. Reported, never silent -- see `syllabus_source` in the response.
    # Set false to map from the outcome text alone.
    use_catalogue: bool = True


@app.post("/api/map")
def compute_mapping(req: MappingRequest) -> dict:
    try:
        fw = get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # A course's own syllabus if one was uploaded; otherwise the university's,
    # which is the same document the department would have gone looking for.
    syllabus, syllabus_source = req.syllabus, {}
    if not syllabus.strip() and req.use_catalogue and (req.course_title or req.course_code):
        syllabus, syllabus_source = syllabus_for(title=req.course_title,
                                                 code=req.course_code)

    pso_levels = {code: pso_action_level(text)
                  for code, text in req.psos.items() if (text or "").strip()}
    action = {**fw.po_action_levels, **pso_levels}

    competency: dict[str, dict[str, int | str]] = req.competency
    convention_cells: list[dict] = []
    if req.lifelong_convention:
        try:
            result = apply_convention(
                {co: {p: c for p, c in row.items()} for co, row in req.competency.items()},
                co_blooms=req.co_blooms, framework_code=fw.code,
                config=ConventionConfig(enabled=True,
                                        competency=req.lifelong_competency),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        competency = result.competency
        convention_cells = [c.to_dict() for c in result.cells]

    course_kind = ""
    core_codes = tuple(req.core_codes)
    if req.course_title or req.course_code:
        course_kind, core_codes = columns_for_course(
            title=req.course_title, code=req.course_code,
            ltp=tuple(req.course_ltp) or None,
            pso_codes=tuple(sorted(pso_levels)),
        )

    # A course on the Constitution of India does not contribute to Problem
    # Analysis, however its verbs read. Applied before the conventions so the
    # core columns are never re-filled behind it.
    withheld: list[tuple[str, str]] = []
    if course_kind:
        competency, withheld = withhold_core_columns(competency, kind=course_kind)

    if req.core_convention:
        evidence = None
        if req.core_require_evidence and req.co_statements:
            evidence = {
                co: signal_strength(text, framework_code=fw.code,
                                    syllabus=syllabus)
                for co, text in req.co_statements.items()
            }
        try:
            core = apply_core_columns(
                competency, co_blooms=req.co_blooms, valid_codes=action,
                config=CoreConfig(enabled=True, codes=core_codes,
                                  on_conflict=req.core_on_conflict,
                                  require_evidence=bool(evidence),
                                  unevidenced=req.core_unevidenced),
                evidence=evidence,
                # Only the POs have a signal lexicon; a PSO is a statement the
                # department wrote and can never be evidenced this way.
                evidence_scope=frozenset(fw.po_codes),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        competency = core.competency
        convention_cells += [c.to_dict() for c in core.cells]

    levels: dict[str, dict[str, int]] = {}
    explanations: list[dict] = []
    for co, po_map in competency.items():
        blooms = req.co_blooms.get(co) or []
        levels[co] = {}
        for po, comp_raw in po_map.items():
            if po not in action:
                known = f"{fw.po_count} POs"
                if pso_levels:
                    known += " + " + ", ".join(sorted(pso_levels))
                raise HTTPException(
                    status_code=400,
                    detail=f"{po} is not a column in {fw.code} ({known})",
                )
            try:
                comp = parse_competency(comp_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            if comp is Competency.NONE:
                continue
            if not blooms and comp is Competency.STRONG:
                raise HTTPException(
                    status_code=400,
                    detail=f"{co} needs at least one Bloom's level for a strong match",
                )
            res = map_co_to_po(po_code=po, competency=comp, co_blooms_levels=blooms,
                               po_action_level=action[po])
            if res.level > 0:
                levels[co][po] = res.level
            explanations.append({
                "co_code": co, "po_code": po, "level": res.level,
                "competency": res.competency,
                "rule_applied": res.rule_applied, "rationale": res.rationale,
                "co_action_level": res.co_action_level, "po_action_level": res.po_action_level,
            })
    return {"framework_code": fw.code, "levels": levels,
            "explanations": explanations,
            "action_levels": action,
            "pso_codes": sorted(pso_levels),
            "convention_cells": convention_cells,
            # Which shape this course was read as, and the columns that
            # follows. Returned rather than applied silently: it is a
            # judgement about the course, and the user has to be able to see
            # it was made and disagree with it.
            # Whether the core columns were weighed against the outcome text
            # or asserted for every one of them. The two produce very
            # different matrices, so the answer travels with the numbers.
            "core_evidence_used": bool(req.core_require_evidence and req.co_statements),
            "course_kind": course_kind,
            "course_kind_label": COURSE_KIND_LABEL.get(course_kind, ""),
            "core_codes": list(core_codes) if req.core_convention else [],
            # Empty when the syllabus came from the upload, populated when it
            # came from AKTU's published one. The document and year travel with
            # it: a 2021 syllabus standing in for a 2026 course is a judgement
            # the department may want to refuse, and they can only refuse what
            # they can see.
            "syllabus_source": syllabus_source,
            "syllabus_chars": len(syllabus.strip()),
            # Cells the scorer proposed and the course kind took back, so the
            # count is visible rather than a silent difference.
            "withheld_cells": [{"co_code": c, "po_code": p} for c, p in withheld]}


# -------------------------------------------------------------- attainment

class QuestionIn(BaseModel):
    question_no: str
    co_code: str
    max_marks: float
    student_marks: dict[str, float]
    # Students who sat the paper but did not attempt this question. Distinct
    # from absentees, who appear in neither collection.
    not_attempted: list[str] = Field(default_factory=list)
    choice_group: str | None = None


class AttainmentRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    course_code: str = "C101"
    course_name: str = "Untitled course"
    questions: list[QuestionIn]
    target_mode: str = TargetMode.FIXED_THRESHOLD.value
    target_pct: float = 60.0
    t1: float = 60.0
    t2: float = 70.0
    t3: float = 80.0
    co_po_levels: dict[str, dict[str, int]] = Field(default_factory=dict)
    # Optional external (semester-end) figures.
    see_meeting_target: int | None = None
    see_appeared: int | None = None
    w_cie: float = 0.30
    w_see: float = 0.70
    co_target: float = 2.0
    home_pos: list[str] = Field(default_factory=list)
    attempt_policy: str = AttemptPolicy.ATTEMPTED_ONLY.value
    # PSO columns. SAR Tables 1.4.2.1 and 3.8.1 carry them alongside the POs,
    # and leaving them empty is an automatic finding.
    pso_codes: list[str] = Field(default_factory=list)
    co_pso_levels: dict[str, dict[str, int]] = Field(default_factory=dict)


@app.post("/api/attainment")
def attainment(req: AttainmentRequest) -> dict:
    if not req.questions:
        raise HTTPException(status_code=400, detail="Add at least one question with marks.")
    try:
        fw = get_framework(req.framework_code)
        mode = TargetMode(req.target_mode)
        policy = AttemptPolicy(req.attempt_policy)
        th = AttainmentThresholds(t1=req.t1, t2=req.t2, t3=req.t3)
        questions = [
            QuestionMarks(q.question_no, q.co_code, q.max_marks, q.student_marks,
                          frozenset(q.not_attempted), q.choice_group)
            for q in req.questions
        ]
        cie = compute_co_attainment_cie(
            questions, mode=mode, target_pct=req.target_pct, thresholds=th,
            policy=policy,
        )
        # Runs unconditionally: a paper with no choice returns an empty list.
        bias = choice_bias_report(questions, mode=mode, target_pct=req.target_pct,
                                  thresholds=th)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    co_codes = sorted(cie)
    see = None
    if req.see_appeared:
        try:
            see = compute_co_attainment_see(
                students_meeting_target=req.see_meeting_target or 0,
                students_appeared=req.see_appeared,
                co_codes=co_codes, thresholds=th,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    direct: dict[str, float] = {}
    for co in co_codes:
        if see is not None:
            try:
                direct[co] = blend_direct(cie[co].level, see[co].level,
                                          w_cie=req.w_cie, w_see=req.w_see)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        else:
            direct[co] = float(cie[co].level)

    course_po = compute_po_attainment_for_course(direct, req.co_po_levels, list(fw.po_codes))
    program = compute_po_direct_attainment({req.course_code: course_po}, list(fw.po_codes))

    # PSO attainment rolls up exactly like PO attainment -- same weighting,
    # same None-for-unaddressed rule -- so the two columns are comparable.
    course_pso = (
        compute_po_attainment_for_course(direct, req.co_pso_levels, req.pso_codes)
        if req.pso_codes else {}
    )
    program_pso = (
        compute_po_direct_attainment({req.course_code: course_pso}, req.pso_codes)
        if req.pso_codes else {}
    )

    cam = course_articulation_matrix(
        course_code=req.course_code, course_name=req.course_name,
        framework_code=fw.code, co_codes=co_codes, co_po_levels=req.co_po_levels,
        pso_codes=req.pso_codes or None, co_pso_levels=req.co_pso_levels or None,
    )
    t381 = po_direct_attainment_table(
        framework_code=fw.code, course_codes=[req.course_code],
        course_po_attainment={req.course_code: {**course_po, **course_pso}},
        direct_attainment={**program, **program_pso},
        pso_codes=req.pso_codes or None,
    )

    # Structural validation and CQI drafting run automatically: the results
    # screen should arrive complete, not require another button.
    validation = validate_course_matrix(
        req.co_po_levels, framework_code=fw.code, home_pos=tuple(req.home_pos)
    )
    question_detail = {
        co: [(q.question_no, q.passed_pct, q.max_marks) for q in cie[co].questions]
        for co in co_codes
    }
    cohort = max((len(q.student_marks) for q in questions), default=0)
    # CO-scope only. A single course's PO attainment is derived from its own
    # COs, so a PO record here would just restate the CO records in different
    # words. PO/PSO improvement (SAR 8.1.2) is a program-level judgement --
    # /api/program-cqi -- where attainment is averaged across every course
    # addressing that PO and a real intervention can be identified.
    cqi = generate_co_cqi(
        direct, target=req.co_target, question_detail=question_detail, cohort_size=cohort
    )

    return {
        "framework": {"code": fw.code, "name": fw.name, "po_codes": list(fw.po_codes)},
        "target_mode": mode.value,
        "attempt_policy": policy.value,
        "cohort_size": cohort,
        "choice_bias": [
            {"co_code": b.co_code, "attempted_only_pct": b.attempted_only_pct,
             "cohort_pct": b.cohort_pct, "gap": b.gap,
             "attempt_rate": round(b.attempt_rate, 3),
             "all_evidence_optional": b.all_evidence_optional,
             "severity": b.severity, "message": b.message}
            for b in bias
        ],
        "validation": {
            "is_valid": validation.is_valid,
            "error_count": len(validation.errors),
            "warning_count": len(validation.warnings),
            "violations": [
                {"rule": v.rule, "severity": v.severity.value,
                 "message": v.message, "cells": v.cells}
                for v in validation.violations
            ],
        },
        "cqi": {
            "summary": summarise(cqi),
            "target": req.co_target,
            "records": [
                {
                    "scope": r.scope, "code": r.code, "attained": r.attained,
                    "target": r.target, "gap": r.gap, "severity": r.severity.value,
                    "drafted_action": r.drafted_action, "rationale": r.rationale,
                    "weakest_questions": [
                        {"question_no": w.question_no, "passed_pct": w.passed_pct,
                         "max_marks": w.max_marks}
                        for w in r.weakest_questions
                    ],
                }
                for r in cqi
            ],
        },
        "co_attainment": [
            {
                "co_code": co,
                "cie_success_pct": round(cie[co].success_pct, 2),
                "cie_level": cie[co].level,
                "see_success_pct": round(see[co].success_pct, 2) if see else None,
                "see_level": see[co].level if see else None,
                "direct": round(direct[co], 2),
                "questions": [
                    {
                        "question_no": q.question_no,
                        "max_marks": q.max_marks,
                        "target_marks": round(q.target_marks, 2),
                        "students_present": q.students_present,
                        "students_meeting_target": q.students_meeting_target,
                        "passed_pct": round(q.passed_pct, 2),
                        "students_attempted": q.students_attempted,
                        "students_not_attempted": q.students_not_attempted,
                    }
                    for q in cie[co].questions
                ],
            }
            for co in co_codes
        ],
        "course_po_attainment": {k: (round(v, 2) if v is not None else None)
                                 for k, v in course_po.items()},
        "course_pso_attainment": {k: (round(v, 2) if v is not None else None)
                                  for k, v in course_pso.items()},
        "tables": {
            "course_articulation_matrix": {
                "number": cam.number, "title": cam.title,
                "headers": cam.headers, "rows": cam.rows,
                "markdown": cam.to_markdown(), "csv": cam.to_csv(),
            },
            "direct_attainment": {
                "number": t381.number, "title": t381.title,
                "headers": t381.headers, "rows": t381.rows,
                "markdown": t381.to_markdown(), "csv": t381.to_csv(),
            },
        },
    }


# ------------------------------------------------------- curriculum library

@app.get("/api/courses")
def list_courses(branch: str | None = None) -> dict:
    return {
        "branches": curriculum.branches(),
        "courses": [
            {
                "code": c.code, "title": c.title, "branch": c.branch,
                "semester": c.semester, "ltp": list(c.ltp), "credits": c.credits,
                "scheme": c.scheme, "units": c.units,
                "home_pos": list(c.home_pos), "ncrf_hours": c.ncrf_hours,
                "sample_paper": c.sample_paper,
                "cos": [
                    {"code": co.code, "statement": co.statement,
                     "blooms_levels": co.blooms_levels}
                    for co in c.cos
                ],
            }
            for c in curriculum.list_courses(branch)
        ],
    }


# ------------------------------------------------------------ CO rewriter

class RewriteRequest(BaseModel):
    cos: dict[str, dict] = Field(
        description="{co_code: {statement: str, blooms_levels: [int]}}"
    )


def _serialise_rewrite(r) -> dict:
    return {
        "co_code": r.co_code, "original": r.original, "proposed": r.proposed,
        "blooms_levels": r.blooms_levels, "rules_applied": r.rules_applied,
        "changed": r.changed, "already_compliant": r.already_compliant,
        "split_suggestion": r.split_suggestion, "note": r.note,
    }


@app.post("/api/rewrite-cos")
def rewrite_cos(req: RewriteRequest) -> dict:
    """
    Rewrite non-compliant CO statements into measurable, action-verb-led form.
    Both versions are returned: the published original stays as provenance,
    the proposal is what faculty accept, edit, or reject.
    """
    if not req.cos:
        raise HTTPException(status_code=400, detail="Add at least one course outcome.")
    cos = {
        code: (str(d.get("statement", "")), list(d.get("blooms_levels", []) or []))
        for code, d in req.cos.items()
    }
    results = rewrite_all(cos)
    changed = [c for c, r in results.items() if r.changed]
    return {
        "rewrites": {c: _serialise_rewrite(r) for c, r in results.items()},
        "summary": {
            "total": len(results),
            "changed": len(changed),
            "already_compliant": sum(1 for r in results.values() if r.already_compliant),
            "changed_codes": changed,
        },
    }


# -------------------------------------------------- automatic mapping (L2)

class ProposeRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    cos: dict[str, dict] = Field(
        description="{co_code: {statement: str, blooms_levels: [int]}}"
    )
    max_pos_per_co: int = 6
    # The course's unit-wise syllabus, as the parser read it out of the same
    # document. Corroborates the outcomes; never speaks for them.
    syllabus: str = ""
    # Stand in AKTU's published syllabus when none was uploaded. Reported in
    # `summary.syllabus_source`, never silent.
    course_title: str = ""
    course_code: str = ""
    use_catalogue: bool = True


@app.post("/api/propose-mapping")
def propose_mapping(req: ProposeRequest) -> dict:
    """
    Propose the whole competency matrix so faculty review rather than author.
    Deterministic and explainable: every proposal carries the terms that fired.
    """
    try:
        get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not req.cos:
        raise HTTPException(status_code=400, detail="Add at least one course outcome.")

    syllabus, syllabus_source = req.syllabus, {}
    if not syllabus.strip() and req.use_catalogue and (req.course_title or req.course_code):
        syllabus, syllabus_source = syllabus_for(title=req.course_title,
                                                 code=req.course_code)

    cos = {
        code: (str(d.get("statement", "")), list(d.get("blooms_levels", []) or []))
        for code, d in req.cos.items()
    }
    try:
        matrix = propose_matrix(cos, framework_code=req.framework_code,
                                max_pos_per_co=req.max_pos_per_co,
                                syllabus=syllabus)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    evidence: dict[str, list[dict]] = {}
    for code, (statement, blooms) in cos.items():
        evidence[code] = [
            {
                "po_code": p.po_code, "competency": p.competency.value,
                "score": p.score, "matched_terms": p.matched_terms,
                "syllabus_terms": p.syllabus_terms,
                "syllabus_score": p.syllabus_score,
                "evidence": p.evidence,
            }
            for p in propose_for_co(statement, framework_code=req.framework_code,
                                    blooms_levels=blooms,
                                    syllabus=syllabus)[:req.max_pos_per_co]
        ]
    proposed = sum(len(v) for v in matrix.values())
    return {
        "framework_code": req.framework_code,
        "competency": matrix,
        "evidence": evidence,
        "summary": {
            "cos": len(matrix),
            "proposed_cells": proposed,
            "unmapped_cos": [c for c, m in matrix.items() if not m],
            "syllabus_chars": len(syllabus.strip()),
            # Empty when the syllabus was uploaded with the course; populated
            # when AKTU's published one stood in for it.
            "syllabus_source": syllabus_source,
        },
    }


# --------------------------------------------------- constraint validation

class ValidateRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    co_po_levels: dict[str, dict[str, int]]
    home_pos: list[str] = Field(default_factory=list)


@app.post("/api/validate-matrix")
def validate_matrix(req: ValidateRequest) -> dict:
    try:
        get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    rep = validate_course_matrix(
        req.co_po_levels, framework_code=req.framework_code,
        home_pos=tuple(req.home_pos),
    )
    return {
        "is_valid": rep.is_valid,
        "error_count": len(rep.errors),
        "warning_count": len(rep.warnings),
        "violations": [
            {"rule": v.rule, "severity": v.severity.value,
             "message": v.message, "cells": v.cells}
            for v in rep.violations
        ],
    }


class ProgramValidateRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    course_matrices: dict[str, dict[str, dict[str, int]]]
    pso_codes: list[str] = Field(default_factory=list)
    min_courses_per_po: int = 2


@app.post("/api/validate-program")
def validate_program(req: ProgramValidateRequest) -> dict:
    try:
        fw = get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    rep = validate_program_matrix(
        req.course_matrices, framework_code=fw.code, po_codes=list(fw.po_codes),
        pso_codes=req.pso_codes, min_courses_per_po=req.min_courses_per_po,
    )
    return {
        "is_valid": rep.is_valid,
        "violations": [
            {"rule": v.rule, "severity": v.severity.value,
             "message": v.message, "cells": v.cells}
            for v in rep.violations
        ],
        "po_course_coverage": po_course_coverage(
            req.course_matrices, list(fw.po_codes), min_level=1
        ),
    }


# ------------------------------------------------------------------ ingest

class IngestRequest(BaseModel):
    text: str
    co_by_question: dict[str, str] = Field(default_factory=dict)
    max_by_question: dict[str, float] = Field(default_factory=dict)


@app.post("/api/ingest-marks")
def ingest(req: IngestRequest) -> dict:
    """Parse a pasted or uploaded marks sheet, detecting its layout."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Nothing to read — the sheet is empty.")
    res = ingest_marks(req.text)
    payload, problems = to_question_payload(
        res, co_by_question=req.co_by_question, max_by_question=req.max_by_question
    )
    return {
        "student_count": res.student_count,
        "columns": [
            {"index": c.index, "header": c.header, "kind": c.kind,
             "question_label": c.question_label}
            for c in res.columns
        ],
        "questions": [
            {"label": c.question_label or c.header, "header": c.header, "index": c.index}
            for c in res.question_columns
        ],
        "preview": res.rows[:8],
        "warnings": res.warnings,
        "skipped_rows": res.skipped_rows,
        "cell_problems": problems[:20],
        "outliers": detect_outliers(payload),
        "payload": payload,
    }


# ------------------------------------------------- program-level roll-up

class ProgramRollupRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    # {course_code: {po_code: attainment}} -- one row per course, as produced
    # by /api/attainment's course_po_attainment.
    course_po_attainment: dict[str, dict[str, float | None]]
    course_matrices: dict[str, dict[str, dict[str, int]]] = Field(default_factory=dict)
    pso_codes: list[str] = Field(default_factory=list)
    po_target: float = 2.0
    min_courses_per_po: int = 2


@app.post("/api/program-rollup")
def program_rollup(req: ProgramRollupRequest) -> dict:
    """
    Program-level PO attainment (SAR Table 3.8.1's average row), the orphan-PO
    check, PO->course coverage for Table 1.3.2, and PO-scope CQI drafts for
    SAR 8.1.2 -- which is where PO improvement actually belongs.
    """
    try:
        fw = get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not req.course_po_attainment:
        raise HTTPException(status_code=400, detail="Add at least one course.")

    po_codes = list(fw.po_codes)
    direct = compute_po_direct_attainment(req.course_po_attainment, po_codes)
    table = po_direct_attainment_table(
        framework_code=fw.code, course_codes=list(req.course_po_attainment),
        course_po_attainment=req.course_po_attainment, direct_attainment=direct,
    )
    validation = validate_program_matrix(
        req.course_matrices, framework_code=fw.code, po_codes=po_codes,
        pso_codes=req.pso_codes, min_courses_per_po=req.min_courses_per_po,
    ) if req.course_matrices else None
    cqi = generate_po_cqi(direct, target=req.po_target)

    return {
        "framework": {"code": fw.code, "name": fw.name, "po_codes": po_codes},
        "direct_attainment": {k: (round(v, 2) if v is not None else None)
                              for k, v in direct.items()},
        "unaddressed_pos": [po for po, v in direct.items() if v is None],
        "table_3_8_1": {
            "number": table.number, "title": table.title,
            "headers": table.headers, "rows": table.rows,
            "markdown": table.to_markdown(), "csv": table.to_csv(),
        },
        "table_1_3_2": po_course_coverage(req.course_matrices, po_codes, min_level=1)
        if req.course_matrices else {},
        "validation": {
            "is_valid": validation.is_valid,
            "violations": [
                {"rule": v.rule, "severity": v.severity.value,
                 "message": v.message, "cells": v.cells}
                for v in validation.violations
            ],
        } if validation else None,
        "cqi": {
            "summary": summarise(cqi),
            "target": req.po_target,
            "records": [
                {"scope": r.scope, "code": r.code, "attained": r.attained,
                 "target": r.target, "gap": r.gap, "severity": r.severity.value,
                 "drafted_action": r.drafted_action, "rationale": r.rationale}
                for r in cqi
            ],
        },
    }


# ----------------------------------------------- syllabus -> outcomes

def _serialise_syllabus(parsed) -> dict:
    return {
        "source": parsed.source,
        "confidence": parsed.confidence,
        "warnings": parsed.warnings,
        "total_cos": parsed.total_cos,
        "courses": [
            {
                "code": c.code, "title": c.title, "label": c.label,
                "ltp": list(c.ltp) if c.ltp else None,
                # The unit-wise course content, so the scorer can corroborate
                # an outcome against what the course actually teaches.
                "units": [{"number": u.number, "title": u.title,
                           "topics": u.topics} for u in c.units],
                # A lab has no unit table -- its experiment list is its
                # syllabus, and it is the only place a lab names its
                # instruments. Sent separately from `units` so the page can
                # say which of the two it read.
                "experiments": c.experiments,
                "syllabus": c.syllabus_text,
                "cos": [
                    {"code": co.code, "statement": co.statement,
                     "blooms_levels": co.blooms_levels,
                     "bloom_inferred": co.bloom_inferred}
                    for co in infer_missing_bloom_levels(c.cos)
                ],
            }
            for c in parsed.courses
        ],
    }


class SyllabusTextRequest(BaseModel):
    text: str


@app.post("/api/parse-syllabus-text")
def parse_syllabus_text(req: SyllabusTextRequest) -> dict:
    """Detect course outcomes in pasted syllabus text."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Nothing to read — paste the CO table.")
    return _serialise_syllabus(parse_text(req.text))


@app.post("/api/parse-syllabus")
async def parse_syllabus_file(file: UploadFile = File(...)) -> dict:
    """
    Detect course outcomes in an uploaded syllabus (.pdf, .docx, .txt).

    A whole-semester AKTU PDF holds a dozen courses, so this returns them
    separately rather than merging outcomes that happen to share a code.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is larger than 25 MB.")
    try:
        parsed = parse_upload(file.filename or "", data)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="That file could not be read. Try a .docx or .pdf export, "
                   "or paste the CO table as text.",
        ) from None
    return _serialise_syllabus(parsed)


# ------------------------------------------------- vague verb replacement

class VerbAdviceRequest(BaseModel):
    statement: str
    blooms_levels: list[int] = Field(default_factory=list)
    limit: int = 6
    use_llm: bool = True


@app.post("/api/verb-advice")
def verb_advice(req: VerbAdviceRequest) -> dict:
    """
    Suggest measurable openings for an outcome with a vague verb, or none.

    `recommended` is the answer, and it needs no API key: it is the top of
    the deterministic ranking together with the reason it came first. `ai` is
    a second opinion that appears only when a key is configured, and its
    absence changes nothing about what the page can do.
    """
    issue = detect_issue(req.statement)
    if not issue:
        return {"issue": None, "phrase": None, "vague_verb": None,
                "suggestions": [], "recommended": None, "ai": None}

    vague = detect_vague_opening(req.statement)
    suggestions = suggest_verbs(req.statement, blooms_levels=req.blooms_levels,
                                limit=max(1, min(req.limit, 13)))
    ai = (ai_recommendation(req.statement, blooms_levels=req.blooms_levels)
          if req.use_llm else None)
    return {
        "issue": issue,
        "phrase": vague or detect_verbless_opening(req.statement),
        "vague_verb": vague,
        "suggestions": [
            {**s.to_dict(), **level_change(req.statement, s.verb,
                                           blooms_levels=req.blooms_levels)}
            for s in suggestions
        ],
        "recommended": offline_recommendation(req.statement,
                                              blooms_levels=req.blooms_levels),
        "ai": ai,
    }


class ApplyVerbRequest(BaseModel):
    statement: str
    verb: str


@app.post("/api/apply-verb")
def apply_verb_route(req: ApplyVerbRequest) -> dict:
    """Rewrite one outcome to open with the chosen verb."""
    try:
        change = level_change(req.statement, req.verb)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"statement": apply_verb(req.statement, req.verb), **change}


# ----------------------------------------- CO x PO document + PO averages

class CoPoDocRequest(BaseModel):
    framework_code: str = "GAPC_V4"
    course_code: str = ""
    course_title: str = ""
    # [{code, statement, blooms_levels, bloom_inferred}]
    cos: list[dict] = Field(default_factory=list)
    co_po_levels: dict[str, dict[str, int]] = Field(default_factory=dict)
    # The department's own PSOs, {code: statement}. A PSO with no statement
    # is not a column -- see /api/copo-workbook.
    psos: dict[str, str] = Field(default_factory=dict)
    # Columns filled by departmental convention rather than inferred; marked
    # with a dagger wherever they appear.
    convention_codes: list[str] = Field(default_factory=list)
    fmt: str = "docx"          # docx | xlsx | csv | md | json


_DOC_MEDIA = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "md": ("text/markdown; charset=utf-8", "md"),
}


def _doc_columns(req: CoPoDocRequest):
    """The PO codes, plus any PSO the department has actually written."""
    fw = get_framework(req.framework_code)
    pso_levels = {code: pso_action_level(text)
                  for code, text in req.psos.items() if (text or "").strip()}
    codes = [*fw.po_codes, *sorted(pso_levels)]
    action = {**fw.po_action_levels, **pso_levels}
    titles = {**fw.po_titles,
              **{c: "Programme Specific Outcome" for c in pso_levels}}
    return fw, codes, action, titles


def _copo_doc_parts(req: CoPoDocRequest):
    try:
        fw, codes, action, titles = _doc_columns(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not req.cos:
        raise HTTPException(status_code=400, detail="Add at least one course outcome.")
    valid = set(codes)
    for co, row in req.co_po_levels.items():
        for po, lv in row.items():
            if po not in valid:
                known = f"{fw.po_count} POs"
                if len(codes) > fw.po_count:
                    known += " + " + ", ".join(codes[fw.po_count:])
                raise HTTPException(
                    status_code=400,
                    detail=f"{po} is not a column in {fw.code} ({known})")
            if int(lv) not in (0, 1, 2, 3):
                raise HTTPException(
                    status_code=400,
                    detail=f"{co} -> {po}: mapping level {lv} is not 0-3")
    return fw, build_copo_document(
        course_code=req.course_code, course_title=req.course_title,
        framework_code=fw.code, po_codes=codes,
        po_titles=titles, po_action_levels=action,
        cos=req.cos, co_po_levels=req.co_po_levels,
        convention_codes=tuple(req.convention_codes))


@app.post("/api/copo-averages")
def copo_averages(req: CoPoDocRequest) -> dict:
    """
    The PO averages as data, so the browser renders exactly what the document
    contains rather than recomputing a mean of its own.
    """
    fw, (tables, notes) = _copo_doc_parts(req)
    _fw, codes, action, titles = _doc_columns(req)
    co_codes = [str(c.get("code", "")) for c in req.cos]
    avgs = po_averages(req.co_po_levels, po_codes=codes, po_titles=titles,
                       po_action_levels=action, co_codes=co_codes)
    return {
        "framework_code": fw.code,
        "course_code": req.course_code,
        "notes": notes,
        "averages": [
            {"po_code": a.po_code, "po_title": a.po_title,
             "po_action_level": a.po_action_level,
             "mapped_cos": a.mapped_cos, "total_cos": a.total_cos,
             "total_level": a.total_level, "level_counts": a.level_counts,
             "average_over_mapped": (None if a.average_over_mapped is None
                                     else round(a.average_over_mapped, 2)),
             "average_over_all": round(a.average_over_all, 2),
             "rounded": a.rounded}
            for a in avgs
        ],
    }


@app.post("/api/copo-document")
def copo_document(req: CoPoDocRequest) -> Response:
    """
    The articulation matrix, the PO averages and the Bloom's levels behind
    them, as one downloadable document.
    """
    fmt = (req.fmt or "docx").lower()
    if fmt not in _DOC_MEDIA:
        raise HTTPException(status_code=400,
                            detail=f"Unknown format {req.fmt!r}. Use docx, xlsx, csv or md.")
    _fw, (tables, notes) = _copo_doc_parts(req)
    stem = f"{req.course_code or 'course'}_CO-PO_{req.framework_code}"
    media, ext = _DOC_MEDIA[fmt]

    if fmt in ("docx", "xlsx"):
        meta = DocumentMeta(
            program=" - ".join(x for x in (req.course_code, req.course_title) if x),
            framework=req.framework_code,
            note=notes[0] if notes else "")
        head = "CO - PO articulation matrix and programme outcome averages"
        try:
            body = (tables_to_docx(tables, meta=meta, notes=notes[1:], title=head)
                    if fmt == "docx"
                    else tables_to_xlsx(tables, meta=meta, notes=notes[1:], title=head))
        except ExportUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
    elif fmt == "csv":
        # One file, tables separated by a blank line and their own caption --
        # a spreadsheet import keeps them apart and a text reader can follow it.
        chunks = [f"Table {t.number}: {t.title}\n{t.to_csv()}" for t in tables]
        chunks.append("Notes\n" + "\n".join(f'"{n}"' for n in notes))
        body = "\n\n".join(chunks).encode("utf-8-sig")
    else:
        chunks = [t.to_markdown() for t in tables]
        chunks.append("\n".join(f"> {n}" for n in notes))
        body = "\n\n".join(chunks).encode("utf-8")

    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'})


# ------------------------------ the department's own mapping workbook

class WorkbookCourse(BaseModel):
    course_code: str = ""
    course_title: str = ""
    # The department's own course serial: "C204" renders its outcomes as
    # C204.1 … C204.5. Nothing in a syllabus carries this, so it is supplied.
    co_prefix: str = ""
    sheet: str = ""
    cos: list[dict] = Field(default_factory=list)
    co_levels: dict[str, dict[str, int]] = Field(default_factory=dict)


class WorkbookRequest(BaseModel):
    framework_code: str = "GAPC_V3"
    courses: list[WorkbookCourse] = Field(default_factory=list)
    # {code: statement} written by the department. No statement, no column.
    psos: dict[str, str] = Field(default_factory=dict)
    pso_action_levels: dict[str, int] = Field(default_factory=dict)
    convention_codes: list[str] = Field(default_factory=list)
    program: str = ""
    academic_year: str = ""
    default_sheet: str = "CO-PO Mapping"
    fmt: str = "xlsx"


@app.post("/api/copo-workbook")
def copo_workbook(req: WorkbookRequest) -> Response:
    """
    Every course in one workbook, laid out the way a department's own CO-PO
    sheet is: a block per course, the course named in the corner cell and
    again on its average row, blank for an unmapped cell, and the average
    over the COs that map rather than over every CO in the course.
    """
    try:
        fw = get_framework(req.framework_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not req.courses:
        raise HTTPException(status_code=400, detail="Add at least one course.")

    pso_codes = [c for c in sorted(req.psos) if (req.psos.get(c) or "").strip()]
    dropped = sorted(set(req.psos) - set(pso_codes))
    codes = [*fw.po_codes, *pso_codes]
    valid = set(codes)

    sheets: dict[str, list[Table]] = {}
    for n, course in enumerate(req.courses, start=1):
        for co, row in course.co_levels.items():
            for col, lv in row.items():
                if col not in valid:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{col} is not a column in {fw.code} "
                               f"({fw.po_count} POs{', ' + ', '.join(pso_codes) if pso_codes else ''})")
                if int(lv) not in (0, 1, 2, 3):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{co} -> {col}: mapping level {lv} is not 0-3")
        sheets.setdefault(course.sheet or req.default_sheet, []).append(
            course_block_table(
                course.cos, course.co_levels, codes=codes,
                course_code=course.course_code, course_title=course.course_title,
                number=str(n), co_prefix=course.co_prefix,
                convention_codes=tuple(req.convention_codes),
            )
        )

    notes = [METHOD_NOTE]
    if req.convention_codes:
        notes.append(convention_note(req.convention_codes))

    # Programme-level coverage, across every block in the file. An empty
    # column is normal for one course and is a finding for a programme, so it
    # is stated here rather than left for a reader to notice by scanning 44
    # blocks sideways. This is the counterweight to filling only the columns
    # the outcomes evidence: withholding a cell is the honest answer to "does
    # this outcome teach that", and it is the wrong answer to "does this
    # programme cover that", which only the whole file can answer.
    touched = {col for c in req.courses for row in c.co_levels.values()
               for col, lv in row.items() if lv}
    empty = [c for c in codes if c not in touched]
    if empty:
        notes.append(
            f"Addressed by no course outcome anywhere in this workbook: "
            f"{', '.join(empty)}. For a single course an empty column is "
            f"normal; across a whole programme it is a gap to close, either "
            f"by revising the outcomes that should carry it or by recording "
            f"the course that does."
        )
    if dropped:
        notes.append(
            f"{', '.join(dropped)} has no statement written by the department, "
            f"so it is not a column here. A PSO column cannot be defended "
            f"without the statement it maps against."
        )

    if (req.fmt or "xlsx").lower() != "xlsx":
        raise HTTPException(status_code=400,
                            detail="The programme workbook is an .xlsx.")
    try:
        body = course_blocks_to_xlsx(
            sheets,
            meta=DocumentMeta(program=req.program,
                              framework=f"{fw.name} ({fw.po_count} POs)"
                                        + (f" + {', '.join(pso_codes)}" if pso_codes else ""),
                              academic_year=req.academic_year),
            notes=notes,
            title="CO - PO / PSO mapping",
        )
    except ExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    stem = f"CO-PO_Mapping_{req.academic_year or fw.code}".replace(" ", "_")
    return Response(
        content=body,
        media_type=_DOC_MEDIA["xlsx"][0],
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


# --------------------------------------------- question -> CO tagging

class TagRequest(BaseModel):
    cos: dict[str, dict] = Field(
        description="{co_code: {statement: str, blooms_levels: [int]}}"
    )
    # Either a whole paper to split, or pre-split questions.
    paper_text: str | None = None
    questions: list[dict] = Field(default_factory=list)
    use_llm: bool = True


@app.get("/api/llm-status")
def llm_state() -> dict:
    """Whether AI arbitration can actually run, stated plainly for the UI."""
    return llm_status()


@app.post("/api/tag-questions")
def tag_questions(req: TagRequest) -> dict:
    """
    Read each question and say which course outcome it assesses.

    A deterministic scorer decides the clear cases; only questions it cannot
    separate are escalated to the model, and each result records which path
    produced it.
    """
    if not req.cos:
        raise HTTPException(status_code=400, detail="Add the course outcomes first.")
    cos = {
        code: (str(d.get("statement", "")), list(d.get("blooms_levels", []) or []))
        for code, d in req.cos.items()
    }
    if req.questions:
        pairs = [(str(q.get("question_no") or f"Q{i+1}"), str(q.get("text", "")))
                 for i, q in enumerate(req.questions)]
        pairs = [(no, t) for no, t in pairs if t.strip()]
    elif req.paper_text and req.paper_text.strip():
        pairs = split_question_paper(req.paper_text)
    else:
        raise HTTPException(
            status_code=400, detail="Paste the question paper, or send questions to tag."
        )
    if not pairs:
        raise HTTPException(status_code=400, detail="No questions were found in that text.")

    status = llm_status()
    tags = (tag_paper_smart(pairs, cos, use_llm=True)
            if (req.use_llm and status["available"]) else tag_paper(pairs, cos))

    return {
        "llm": {"used": req.use_llm and status["available"], **status},
        "tags": [
            {
                "question_no": t.question_no, "question_text": t.question_text,
                "co_code": t.co_code, "confidence": t.confidence,
                "method": t.method, "model": t.model, "rationale": t.rationale,
                "needs_review": t.needs_review,
                "scores": [
                    {"co_code": s.co_code, "score": s.score,
                     "overlap": s.overlap, "verb_match": s.verb_match}
                    for s in t.scores[:4]
                ],
            }
            for t in tags
        ],
        "summary": {
            "total": len(tags),
            "confident": sum(1 for t in tags if not t.needs_review),
            "needs_review": sum(1 for t in tags if t.needs_review),
            "unsure": sum(1 for t in tags if t.confidence == "unsure"),
            "llm_arbitrated": sum(1 for t in tags if t.method == "llm-arbitrated"),
        },
    }


@app.get("/api/admin/workbook")
def admin_workbook(admin=Depends(require_admin), fmt: str = "xlsx") -> Response:
    """
    Every submitted mapping as one file, laid out the way the department's own
    CO-PO sheet is.

    This is the artefact they already keep by hand -- a block per course, the
    course named in its corner cell and again on its average row. It is built
    from the same function the offline page uses, so the sheet a teacher
    downloads for one subject and the sheet the admin downloads for all of
    them are the same document at different scales.
    """
    from app.store import neon                          # noqa: PLC0415

    rows = neon.all_submissions()
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="No teacher has submitted a mapping yet.")

    courses, framework = [], "GAPC_V3"
    for r in rows:
        payload = r.get("payload") or {}
        framework = r.get("framework_code") or framework
        courses.append(WorkbookCourse(
            course_code=r.get("code") or "",
            course_title=r.get("title") or "",
            co_prefix=r.get("co_prefix") or "",
            sheet=r.get("academic_year") or "",
            cos=payload.get("cos") or [],
            co_levels=payload.get("co_levels") or {},
        ))
    years = sorted({(r.get("academic_year") or "") for r in rows} - {""})
    return copo_workbook(WorkbookRequest(
        framework_code=framework, courses=courses,
        program="B.Tech", academic_year=", ".join(years),
        default_sheet="CO-PO Mapping", fmt=fmt))


@app.get("/api/health")
def health() -> dict:
    """Liveness, and whether the shared database is answering.

    Separate fields on purpose: the mapping engine is up whether or not the
    database is, and a monitor that conflates the two pages somebody at three
    in the morning for a sleeping Postgres.
    """
    from app.store import neon                              # noqa: PLC0415

    db = "unlinked"
    if neon.is_configured():
        try:
            neon.ensure_schema()
            db = "ready"
        except Exception:                                   # noqa: BLE001
            db = "waking"
    return {"status": "ok", "ai_in_attainment_path": False, "database": db}


# ------------------------------------------------------------------- static

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # The three pages the department signs in to. Served as files rather than
    # guarded here: each one asks /api/auth/me on load and sends the visitor
    # back to /login if it answers 401, and every route the pages call is
    # guarded on the server. Serving the HTML is not the access control.
    @app.get("/login")
    def login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/admin")
    def admin_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/teacher")
    def teacher_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "teacher.html")

    @app.get("/attainment")
    def attainment_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "attainment.html")
