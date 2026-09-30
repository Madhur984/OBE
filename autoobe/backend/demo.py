"""
End-to-end demo of the automated pipeline, with zero human input.

Takes a course straight from the curriculum library and runs it all the way to
SAR tables: rewrite non-compliant outcomes, infer competency, derive mapping
levels, validate the matrix structurally, compute attainment from marks, and
draft Criterion 8 improvement actions.

    python demo.py
"""
from __future__ import annotations

import sys

# The Windows console defaults to cp1252, which cannot encode the arrows and
# middots below. Reconfigure rather than dropping to ASCII, so the output
# reads the same on every platform.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.engine.attainment import (  # noqa: E402
    AttainmentThresholds, QuestionMarks, TargetMode,
    compute_co_attainment_cie, compute_po_attainment_for_course,
    compute_po_direct_attainment,
)
from app.engine.co_rewriter import rewrite_co
from app.engine.competency import Competency, propose_matrix
from app.engine.constraints import validate_course_matrix, validate_program_matrix
from app.engine.cqi import generate_co_cqi, generate_po_cqi
from app.engine.frameworks import get_framework
from app.engine.ingest import ingest_marks, to_question_payload
from app.engine.mapping_rules import map_co_to_po
from app.engine.sar_export import course_articulation_matrix, po_direct_attainment_table
from app.seed.curriculum import AKTU_RAW_SAMPLES, BY_CODE

RAW_SHEET = """Roll No.,Name,Q1,Q2,Q3,Q4,Total
21CS001,Asha,8,7,12,14,41
21CS002,Ravi,7,6,9,11,33
21CS003,Neha,9,8,14,18,49
21CS004,Amit,6,5,7,8,26
21CS005,Priya,5,4,6,7,22
21CS006,Karan,10,9,15,19,53
21CS007,Divya,7,6,10,12,35
21CS008,Rahul,8,7,11,13,39
21CS009,Sana,4,3,5,6,18
21CS010,Vikram,9,8,13,16,46
21CS011,Meera,6,5,8,9,28
21CS012,Arjun,7,AB,9,11,27
Total,,86,68,119,144,417"""


def rule(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def main() -> None:
    fw = get_framework("GAPC_V4")
    course = BY_CODE["KEC601"]
    th = AttainmentThresholds()

    rule("0 · AUTO-CORRECT NON-COMPLIANT OUTCOMES")
    print("  Real statements from a current AKTU syllabus:\n")
    for raw in AKTU_RAW_SAMPLES:
        r = rewrite_co("COx", raw)
        print(f"    was: {r.original}")
        print(f"    now: {r.proposed}")
        print(f"    why: {' · '.join(r.rules_applied)}\n")

    rule(f"1 · COURSE FROM THE LIBRARY — {course.code} {course.title}")
    cos = {co.code: (co.statement, co.blooms_levels) for co in course.cos}
    for code, (stmt, k) in cos.items():
        print(f"  {code} (K{','.join(map(str, k))}): {stmt}")
    h = course.ncrf_hours
    print(f"\n  L-T-P {course.ltp} → NCrF hours: CI={h['CI']} LI={h['LI']} "
          f"TW+SL={h['TW_SL']} (total {h['total']} = {course.credits} credits × 30)")
    print("  AKTU publishes L-T-P only; SAR Table 1.2.1.1 needs these hours.")

    rule("2 · AUTO-PROPOSE COMPETENCY, THEN DERIVE LEVELS")
    matrix = propose_matrix(cos, framework_code=fw.code)
    levels: dict[str, dict[str, int]] = {}
    for co, pos in matrix.items():
        levels[co] = {}
        for po, comp in pos.items():
            res = map_co_to_po(po_code=po, competency=Competency(comp),
                               co_blooms_levels=cos[co][1],
                               po_action_level=fw.po_action_levels[po])
            if res.level:
                levels[co][po] = res.level
        cells = "  ".join(f"{p}={levels[co][p]}" for p in sorted(levels[co]))
        print(f"  {co}: {cells}")
    print(f"\n  {sum(len(v) for v in levels.values())} cells proposed with no human input.")

    rule("3 · STRUCTURAL CHECKS")
    rep = validate_course_matrix(levels, framework_code=fw.code, home_pos=course.home_pos)
    print(f"  valid: {rep.is_valid}   errors: {len(rep.errors)}   warnings: {len(rep.warnings)}")
    for v in rep.violations:
        print(f"    [{v.severity.value}] {v.rule}: {v.message[:88]}")
    if not rep.violations:
        print("    No structural problems found.")

    rule("4 · INGEST A MESSY MARKS SHEET (Name and Total columns, an absentee)")
    ing = ingest_marks(RAW_SHEET)
    print(f"  detected {ing.student_count} students, "
          f"{len(ing.question_columns)} question columns: "
          f"{[c.question_label for c in ing.question_columns]}")
    print(f"  ignored columns: {[c.header for c in ing.columns if c.kind == 'ignored']}")
    print(f"  skipped rows: {ing.skipped_rows}")

    co_by_q = {f"Q{i+1}": co for i, co in enumerate(list(cos)[:4])}
    max_by_q = {"Q1": 10, "Q2": 10, "Q3": 15, "Q4": 20}
    payload, problems = to_question_payload(ing, co_by_question=co_by_q,
                                            max_by_question=max_by_q)
    print(f"  unreadable cells: {problems or 'none'}")
    print("  (21CS012 was absent on Q2 — excluded from that question, not scored zero)")

    rule("5 · ATTAINMENT — BOTH TARGET MODES")
    questions = [QuestionMarks(q["question_no"], q["co_code"], q["max_marks"],
                               q["student_marks"]) for q in payload]
    for mode in (TargetMode.FIXED_THRESHOLD, TargetMode.CLASS_AVERAGE):
        res = compute_co_attainment_cie(questions, mode=mode, target_pct=60.0, thresholds=th)
        line = "  ".join(f"{c}={r.success_pct:5.1f}%(L{r.level})" for c, r in sorted(res.items()))
        print(f"  {mode.value:<16} {line}")

    cie = compute_co_attainment_cie(questions, mode=TargetMode.FIXED_THRESHOLD,
                                    target_pct=60.0, thresholds=th)
    direct = {co: float(r.level) for co, r in cie.items()}

    rule("6 · SAR TABLE 1.4.2.1 — COURSE ARTICULATION MATRIX")
    print(course_articulation_matrix(
        course_code=course.code, course_name=course.title, framework_code=fw.code,
        co_codes=sorted(cos), co_po_levels=levels).to_markdown())

    rule("7 · PROGRAM ROLL-UP — SAR TABLE 3.8.1")
    course_po = compute_po_attainment_for_course(direct, levels, list(fw.po_codes))
    per_course = {
        course.code: course_po,
        "KCS502": {**{p: None for p in fw.po_codes}, "PO1": 2.4, "PO2": 2.1, "PO5": 1.8},
        "KCS503": {**{p: None for p in fw.po_codes}, "PO1": 2.9, "PO3": 2.2, "PO2": 2.6},
    }
    program = compute_po_direct_attainment(per_course, list(fw.po_codes))
    print(po_direct_attainment_table(
        framework_code=fw.code, course_codes=list(per_course),
        course_po_attainment=per_course, direct_attainment=program).to_markdown())

    prog = validate_program_matrix({course.code: levels}, framework_code=fw.code,
                                   po_codes=list(fw.po_codes))
    orphans = [v.cells[0] for v in prog.errors if v.rule == "orphan_po"]
    print(f"\n  Unaddressed POs across this program slice: {', '.join(orphans) or 'none'}")
    print("  Each is an automatic NBA finding, so they stay visible rather than reading as 0.")

    rule("8 · CRITERION 8 — DRAFTED IMPROVEMENT ACTIONS")
    qdetail = {co: [(q.question_no, q.passed_pct, q.max_marks) for q in r.questions]
               for co, r in cie.items()}
    for rec in generate_co_cqi(direct, target=2.0, question_detail=qdetail,
                               cohort_size=ing.student_count):
        print(f"  [{rec.severity.value:8}] {rec.code}: {rec.rationale}")
        print(f"             {rec.drafted_action[:150]}…\n")
    for rec in generate_po_cqi(program, target=2.0):
        print(f"  [{rec.severity.value:8}] {rec.code}: {rec.rationale}")

    demo_psos(cos)
    demo_indirect(fw, direct, levels,
                  {code: stmt for code, (stmt, _) in cos.items()})
    demo_choice_bias(payload, th)
    demo_trends(program)
    demo_migration(levels_v3(cos), cos)
    demo_evidence(course, direct, levels)

    print("\n" + "=" * 78)
    print("From a course code to signed, stored, auditable SAR tables with no")
    print("human input — and every number produced by deterministic")
    print("arithmetic, with no model anywhere in that path.")
    print("=" * 78)


# ---------------------------------------------------------------------------
# The capabilities added after the first pipeline: PSOs, indirect assessment,
# choice questions, trends, migration, and the stored evidence bundle.
# ---------------------------------------------------------------------------

def demo_psos(cos: dict) -> None:
    from app.engine import pso as P

    rule("9 · PROGRAM SPECIFIC OUTCOMES — inferred, not authored")
    psos = [
        P.PSO("PSO1", "Apply signal processing and communication principles to "
                      "design electronic systems for real-world applications."),
        P.PSO("PSO2", "Use modern EDA tools to simulate, verify and implement "
                      "analog and digital circuits."),
    ]
    bad = P.check_pso("PSO3", "Apply engineering knowledge to solve complex "
                              "engineering problems in society.")
    print("  A PSO that is really a PO is caught before it reaches a table:")
    for f in bad.findings:
        if f.severity == "error":
            print(f"    [{f.severity}] {f.message[:96]}…")

    print("\n  Inferred CO × PSO cells:")
    for m in P.propose_pso_matrix(cos, psos):
        print(f"    {m.co_code} → {m.pso_code}  level {m.level}   {m.evidence[:58]}")


def demo_indirect(fw, direct: dict, levels: dict, statements: dict) -> None:
    from app.engine import indirect as ind

    rule("10 · INDIRECT ASSESSMENT — survey written from the outcomes")
    inst = ind.build_course_exit_survey(statements, course_title="Digital Communication")
    print(f"  Instrument: {len(inst.items)} items, {inst.scale_max}-point scale.")
    print(f"    e.g. {inst.items[0].text}")

    # A plausible cohort: mostly agreement, some dissent.
    responses = [{i.id: 4 for i in inst.items} for _ in range(40)]
    responses += [{i.id: 2 for i in inst.items} for _ in range(15)]
    res = ind.score_survey(inst, responses, population=120)
    print(f"\n  {res.responses} responses of {res.population} → adequacy: {res.adequacy}")
    print(f"  Scale floor scores 0%, not {100 / inst.scale_max:.0f}% — "
          f"mean 3.45 → {res.items[0].pct:.1f}%")

    co_levels = {k: float(v) for k, v in res.levels().items()}
    indirect_po = ind.rollup_indirect_to_po(co_levels, levels, list(fw.po_codes))
    blended = ind.blend_attainment(direct_po_only(direct, levels, fw), indirect_po)
    print("\n  PO      direct  indirect  overall")
    for b in blended:
        if b.overall is None:
            continue
        d = f"{b.direct:.2f}" if b.direct is not None else "  – "
        i = f"{b.indirect:.2f}" if b.indirect is not None else "  – "
        print(f"  {b.target_code:<6}  {d:>5}    {i:>5}     {b.overall:.2f}")
    print(f"\n  {ind.methodology_note(w_direct=0.8, w_indirect=0.2, scale_max=5)[:180]}…")


def direct_po_only(direct: dict, levels: dict, fw) -> dict:
    from app.engine.attainment import compute_po_attainment_for_course
    return compute_po_attainment_for_course(direct, levels, list(fw.po_codes))


def demo_choice_bias(payload: list, th) -> None:
    from app.engine.attainment import (
        AttemptPolicy, QuestionMarks, TargetMode, choice_bias_report,
        compute_co_attainment_cie,
    )

    rule("11 · CHOICE QUESTIONS — 'attempt any two' is not absence")
    # Take the real payload and make one question optional, as a real paper does.
    qs = []
    for i, q in enumerate(payload):
        marks = dict(q["student_marks"])
        skipped: set[str] = set()
        if i == 1:                       # the weakest students skip this one
            weakest = sorted(marks, key=lambda r: marks[r])[:5]
            for r in weakest:
                skipped.add(r)
                marks.pop(r)
        qs.append(QuestionMarks(q["question_no"], q["co_code"], q["max_marks"],
                                marks, frozenset(skipped),
                                "G1" if skipped else None))

    for policy in (AttemptPolicy.ATTEMPTED_ONLY, AttemptPolicy.COHORT):
        out = compute_co_attainment_cie(qs, mode=TargetMode.FIXED_THRESHOLD,
                                        target_pct=60, thresholds=th, policy=policy)
        vals = "  ".join(f"{co}:{r.success_pct:5.1f}%" for co, r in sorted(out.items()))
        print(f"  {policy.value:<15} {vals}")

    for b in choice_bias_report(qs, mode=TargetMode.FIXED_THRESHOLD,
                                target_pct=60, thresholds=th):
        print(f"\n  [{b.severity}] {b.message}")


def demo_trends(program: dict) -> None:
    from app.engine import trends as T

    rule("12 · CRITERION 8 OVER TIME — including across the framework change")
    latest = {k: v for k, v in program.items() if v is not None}
    legacy = {}
    for code, v in latest.items():
        n = int(code[2:])
        if n <= 5:
            legacy[code] = round(v * 0.8, 2)
        elif n == 6:
            legacy["PO6"], legacy["PO7"] = round(v * 0.8, 2), round(v * 0.7, 2)
        else:
            legacy[f"PO{n + 1}"] = round(v * 0.8, 2)

    runs = [
        {"academic_year": "2023-24", "created_at": "2024-06-01",
         "config": {"framework_code": "GAPC_V3"},
         "result": {"po_direct_attainment": legacy}},
        {"academic_year": "2025-26", "created_at": "2026-06-01",
         "config": {"framework_code": "GAPC_V4"},
         "result": {"po_direct_attainment": latest}},
    ]
    series = T.build_series(runs, targets={c: 2.0 for c in latest})
    for s in series:
        if s.latest is None:
            continue
        pts = " → ".join("–" if p.value is None else f"{p.value:.2f}" for p in s.points)
        merged = "  (merged from 2 legacy outcomes)" if s.has_merged_points else ""
        print(f"  {s.target_code:<6} {pts:<18} {s.direction}{merged}")

    print()
    for f in T.gap_analysis(series)[:3]:
        print(f"  [{f.severity}] {f.message[:150]}")

    for c in T.check_closure(series, [{"target_code": "PO1",
                                       "action": "Added a design-led mini project",
                                       "academic_year": "2023-24"}]):
        print(f"\n  [{c.verdict}] {c.message[:170]}")


def levels_v3(cos: dict) -> dict:
    """Derive a legacy 12-PO matrix from the same outcomes, to migrate."""
    from app.engine.competency import Competency, propose_matrix
    from app.engine.frameworks import get_framework
    from app.engine.mapping_rules import map_co_to_po

    fw3 = get_framework("GAPC_V3")
    matrix = propose_matrix(cos, framework_code="GAPC_V3")
    out: dict[str, dict[str, int]] = {}
    for co, per_po in matrix.items():
        row = {}
        for po, comp in per_po.items():
            r = map_co_to_po(po_code=po, competency=Competency(comp),
                             co_blooms_levels=cos[co][1] or [2],
                             po_action_level=fw3.po_action_levels[po])
            if r.level:
                row[po] = r.level
        out[co] = row
    return out


def demo_migration(legacy: dict, cos: dict) -> None:
    from app.engine import migration as M

    rule("13 · MIGRATING A LEGACY 12-PO MATRIX")
    result = M.migrate_and_compare(legacy, cos)
    print(f"  Agreement with a fresh derivation: {result.agreement_rate:.0%}")
    print(f"  {result.recommendation}\n")
    for d in result.migrated.merged_cells:
        print(f"  {d.message[:150]}")
    for w in result.migrated.warnings:
        print(f"  {w[:150]}")


def demo_evidence(course, direct: dict, levels: dict) -> None:
    from app.store.audit import build_evidence_bundle, record, verify_chain
    from app.store.db import Database
    from app.store import workflow

    rule("14 · STORED, SIGNED, AND VERIFIABLE")
    db = Database(":memory:")
    db.create_tenant("Demo Institute", tenant_id="demo")
    s = db.tenant("demo")
    pid = s.create_program("ECE-BTECH", "B.Tech ECE", "GAPC_V4", branch="ECE")
    cid = s.create_course(pid, course.code, course.title, semester=course.semester)
    s.set_cos(cid, [(c.code, c.statement, c.blooms_levels, None) for c in course.cos])
    for co, row in levels.items():
        for po, lvl in row.items():
            s.set_mapping(cid, co, "PO", po, competency="strong", level=lvl,
                          source="proposed", framework_code="GAPC_V4")
    run = s.save_run(academic_year="2025-26", scope="course", course_id=cid,
                     program_id=pid, created_by="faculty@college.edu",
                     config={"t1": 60, "t2": 70, "t3": 80},
                     result={"co_attainment": {}, "po_direct_attainment": direct})
    record(s, actor="faculty@college.edu", action="attainment.saved",
           entity_type="course_matrix", entity_id=course.code, payload={"run": run})

    for action, role in (("submit", "faculty"), ("approve", "hod"), ("lock", "coordinator")):
        st = workflow.transition(s, entity_type="course_matrix", entity_id=course.code,
                                 action=action, actor=f"{role}@college.edu", role=role)
        print(f"  {role:<12} → {st.state}")

    try:
        workflow.transition(s, entity_type="course_matrix", entity_id=course.code,
                            action="submit", actor="faculty@college.edu", role="faculty")
    except workflow.TransitionDenied as exc:
        print(f"\n  Locked: {str(exc)[:140]}")

    bundle = build_evidence_bundle(s, course_id=cid, academic_year="2025-26")
    print(f"\n  Evidence bundle: {len(bundle.outcomes)} outcomes, "
          f"{len(bundle.mapping)} mapping cells, {len(bundle.audit)} audit events.")
    print(f"  Chain: {bundle.chain['summary']}")

    # Now tamper with it, the way a quiet edit before a visit would.
    s._write("UPDATE audit_events SET payload = ? WHERE tenant_id = ? AND seq = 1",
             ('{"run": "something-else"}', "demo"))
    check = verify_chain(s)
    print(f"  After a single altered row: {check.summary}")
    print(f"    → {check.problems[0]}")
    db.close()


if __name__ == "__main__":
    main()
