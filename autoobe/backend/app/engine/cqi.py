"""
Auto-CQI -- SAR Criterion 8, "Actions Taken Based on the Results of
Evaluation of the COs, POs, and PSOs" (40 of the criterion's 80 marks).

Criterion 8 is where affiliated colleges lose the most marks, because 8.1.2
asks for measures implemented *during two years*. A college that starts
collecting this three months before a visit cannot manufacture it -- which is
also why the value of this feature compounds with time on the platform.

When a CO or PO misses its target, this generates a draft record: the gap, the
weakest contributing questions, the affected cohort, and a proposed action
grounded in what the data actually shows. Faculty approve or edit. Nothing
here writes to a SAR unreviewed, and nothing here invents a number -- every
figure is carried through from the attainment engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class GapSeverity(str, Enum):
    CRITICAL = "critical"   # attained 0 against a target of 2+
    MAJOR = "major"         # short by a full level or more
    MINOR = "minor"         # short by less than a level


@dataclass(frozen=True)
class WeakQuestion:
    question_no: str
    passed_pct: float
    max_marks: float


@dataclass(frozen=True)
class CQIRecord:
    scope: str                    # "CO" or "PO"
    code: str
    attained: float
    target: float
    gap: float
    severity: GapSeverity
    weakest_questions: list[WeakQuestion] = field(default_factory=list)
    cohort_size: int = 0
    drafted_action: str = ""
    rationale: str = ""


def _severity(attained: float, target: float) -> GapSeverity:
    gap = target - attained
    if attained == 0 and target >= 2:
        return GapSeverity.CRITICAL
    return GapSeverity.MAJOR if gap >= 1.0 else GapSeverity.MINOR


def _co_action(code: str, weak: list[WeakQuestion], severity: GapSeverity) -> str:
    """
    Draft an action that names the evidence. Generic CQI prose ("remedial
    classes were conducted") is what peer teams discount; specificity is the
    whole point.
    """
    if not weak:
        base = (
            f"Review the delivery and assessment of {code}. Attainment fell short of "
            f"target across the assessments as a whole rather than at any single "
            f"question, which usually points at pace or prerequisite gaps rather "
            f"than one topic."
        )
    else:
        qs = ", ".join(f"{w.question_no} ({w.passed_pct:.0f}% reached target)" for w in weak)
        base = (
            f"Attainment of {code} was pulled down by {qs}. Re-teach the underlying "
            f"topic with worked examples, and add a formative check before the next "
            f"internal assessment so the gap surfaces earlier."
        )
    if severity is GapSeverity.CRITICAL:
        base += (
            " Attainment is zero against a substantive target, so treat this as a "
            "curriculum-delivery issue and not a cohort anomaly: confirm the topic "
            "was actually delivered and assessed at the intended cognitive level."
        )
    return base


def _po_action(code: str, attained: float, target: float) -> str:
    return (
        f"{code} attained {attained:.2f} against a target of {target:.2f}. Identify "
        f"which contributing courses sit lowest, and address the gap through "
        f"curriculum intervention (content beyond syllabus, targeted labs or "
        f"mini-projects mapped to {code}) rather than by re-weighting the matrix. "
        f"Record the measure and re-measure it in the following cycle so the "
        f"closed loop is evidenced across two years, per SAR 8.1.2."
    )


def generate_co_cqi(
    co_attainment: dict[str, float],
    *,
    target: float,
    question_detail: dict[str, list[tuple[str, float, float]]] | None = None,
    cohort_size: int = 0,
    weak_question_threshold: float = 60.0,
    max_weak_questions: int = 3,
) -> list[CQIRecord]:
    """
    One record per CO under target. `question_detail` maps a CO to
    (question_no, passed_pct, max_marks) tuples so the draft can cite the
    specific questions that dragged attainment down.
    """
    records: list[CQIRecord] = []
    for code, attained in sorted(co_attainment.items()):
        if attained >= target:
            continue
        weak: list[WeakQuestion] = []
        for qno, passed, mx in sorted(
            (question_detail or {}).get(code, []), key=lambda t: t[1]
        ):
            if passed < weak_question_threshold and len(weak) < max_weak_questions:
                weak.append(WeakQuestion(qno, passed, mx))

        sev = _severity(attained, target)
        records.append(CQIRecord(
            scope="CO", code=code, attained=round(attained, 2), target=target,
            gap=round(target - attained, 2), severity=sev, weakest_questions=weak,
            cohort_size=cohort_size, drafted_action=_co_action(code, weak, sev),
            rationale=(
                f"{code} attained {attained:.2f} against a target of {target:.2f} "
                f"(short by {target - attained:.2f})."
            ),
        ))
    return records


def generate_po_cqi(
    po_attainment: dict[str, float | None],
    *,
    target: float,
) -> list[CQIRecord]:
    """One record per PO under target. Unaddressed POs are skipped here --
    an orphan PO is a curriculum-gap finding, not a CQI action, and the
    constraint solver reports it separately."""
    records: list[CQIRecord] = []
    for code, attained in sorted(po_attainment.items()):
        if attained is None or attained >= target:
            continue
        records.append(CQIRecord(
            scope="PO", code=code, attained=round(attained, 2), target=target,
            gap=round(target - attained, 2), severity=_severity(attained, target),
            drafted_action=_po_action(code, attained, target),
            rationale=(
                f"{code} attained {attained:.2f} against a target of {target:.2f} "
                f"(short by {target - attained:.2f})."
            ),
        ))
    return records


def summarise(records: list[CQIRecord]) -> dict[str, int]:
    out = {"total": len(records), "critical": 0, "major": 0, "minor": 0}
    for r in records:
        out[r.severity.value] += 1
    return out
