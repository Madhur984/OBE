"""
Attainment engine -- deterministic, no AI anywhere in this path.

Every number that can reach a submitted SAR is produced here by explicit
arithmetic, per AutoOBE_v3 Section 8. Verified line-by-line against the
official NBA SAR (GAPC v4.0, Jan 2025):

  * SAR 3.7.2  -- two target modes, verbatim: "Target may be stated in terms
    of percentage of students getting more than class average marks or set
    by the program."
  * SAR 3.8.1  -- verbatim note: "Direct attainment of a PO/PSO is determined
    by taking average across all courses addressing that PO/PSO."

Pure functions over plain data structures: no database access, so the
regression suite can pin exact expected values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TargetMode(str, Enum):
    FIXED_THRESHOLD = "fixed_threshold"   # SAR 3.7.2, "set by the program"
    CLASS_AVERAGE = "class_average"       # SAR 3.7.2, "more than class average marks"


class AttemptPolicy(str, Enum):
    """
    How an optional question ("attempt any two of five") is scored.

    This is not a preference. Absence and non-attempt look identical in a
    marks sheet -- both are a blank cell -- but they mean opposite things.
    An absent student was never measured. A present student who skipped Q3
    *was* measured: they chose not to answer it, which is information.

    ATTEMPTED_ONLY  Score on the students who attempted. Honest for that
                    question, but a CO evidenced only by optional questions is
                    then measured on a self-selected subset -- the students who
                    felt weakest on it are precisely the ones missing -- so it
                    reads high.
    COHORT          A present student who did not attempt counts as not having
                    met the target. Conservative, and defensible when the
                    choice set covers one CO, since every student had a fair
                    opportunity to demonstrate it.

    Neither is universally right, so the engine computes both and reports the
    gap (see `choice_bias_report`). A college picks a policy and declares it;
    what it must not do is pick one without knowing what the other would say.
    """
    ATTEMPTED_ONLY = "attempted_only"
    COHORT = "cohort"


@dataclass(frozen=True)
class QuestionMarks:
    """One question and every present student's mark on it."""
    question_no: str
    co_code: str
    max_marks: float
    student_marks: dict[str, float]  # roll number -> marks (absentees excluded)
    # Rolls of students who sat the assessment but did not attempt this
    # question. Empty for a compulsory question. Distinct from absentees,
    # who are simply not present in either collection.
    not_attempted: frozenset[str] = frozenset()
    # Questions sharing a group are alternatives; None means compulsory.
    choice_group: str | None = None

    def __post_init__(self) -> None:
        if self.max_marks <= 0:
            raise ValueError(f"Question {self.question_no}: max_marks must be > 0")
        for roll, m in self.student_marks.items():
            if m < 0 or m > self.max_marks:
                raise ValueError(
                    f"Question {self.question_no}, roll {roll}: mark {m} outside 0..{self.max_marks}"
                )
        overlap = set(self.not_attempted) & set(self.student_marks)
        if overlap:
            raise ValueError(
                f"Question {self.question_no}: {sorted(overlap)} appear both as "
                f"having a mark and as not attempted."
            )

    @property
    def is_optional(self) -> bool:
        return self.choice_group is not None or bool(self.not_attempted)


@dataclass(frozen=True)
class AttainmentThresholds:
    """Tenant config with effective-from dates upstream; plain values here."""
    t1: float = 60.0   # >= t1 -> level 1
    t2: float = 70.0   # >= t2 -> level 2
    t3: float = 80.0   # >= t3 -> level 3

    def __post_init__(self) -> None:
        if not (0 < self.t1 <= self.t2 <= self.t3 <= 100):
            raise ValueError("Thresholds must satisfy 0 < t1 <= t2 <= t3 <= 100")


@dataclass(frozen=True)
class QuestionOutcome:
    question_no: str
    co_code: str
    max_marks: float
    target_marks: float
    students_present: int          # the denominator actually used
    students_meeting_target: int
    passed_pct: float
    students_attempted: int = 0
    students_not_attempted: int = 0
    policy: str = AttemptPolicy.ATTEMPTED_ONLY.value

    @property
    def attempt_rate(self) -> float | None:
        total = self.students_attempted + self.students_not_attempted
        if total == 0:
            return None
        return self.students_attempted / total


@dataclass(frozen=True)
class COAttainment:
    co_code: str
    success_pct: float
    level: int
    questions: list[QuestionOutcome] = field(default_factory=list)

    @property
    def evidence_is_optional(self) -> bool:
        """Every question behind this CO was one students could skip."""
        return bool(self.questions) and all(
            q.students_not_attempted > 0 for q in self.questions
        )


def compute_target_marks(q: QuestionMarks, mode: TargetMode, target_pct: float) -> float:
    if mode is TargetMode.FIXED_THRESHOLD:
        if not (0 < target_pct <= 100):
            raise ValueError(f"target_pct must be in (0, 100], got {target_pct!r}")
        return q.max_marks * (target_pct / 100.0)
    if not q.student_marks:
        raise ValueError(
            f"Question {q.question_no}: CLASS_AVERAGE target needs at least one present student"
        )
    return sum(q.student_marks.values()) / len(q.student_marks)


def evaluate_question(
    q: QuestionMarks,
    mode: TargetMode,
    target_pct: float,
    policy: AttemptPolicy = AttemptPolicy.ATTEMPTED_ONLY,
) -> QuestionOutcome:
    target = compute_target_marks(q, mode, target_pct)
    attempted = len(q.student_marks)
    skipped = len(q.not_attempted)
    if attempted == 0:
        raise ValueError(f"Question {q.question_no}: no students present")
    met = sum(1 for m in q.student_marks.values() if m >= target)

    # Under COHORT the denominator widens to everyone who sat the paper; the
    # numerator does not, because a student who did not answer did not
    # demonstrate the outcome. The class-average target itself is still taken
    # over attempters only -- averaging in scores nobody earned would move the
    # target, not just the pass count.
    denominator = attempted + skipped if policy is AttemptPolicy.COHORT else attempted

    return QuestionOutcome(
        question_no=q.question_no, co_code=q.co_code, max_marks=q.max_marks,
        target_marks=target, students_present=denominator, students_meeting_target=met,
        passed_pct=met / denominator * 100.0,
        students_attempted=attempted, students_not_attempted=skipped,
        policy=policy.value,
    )


def level_for(success_pct: float, th: AttainmentThresholds) -> int:
    if success_pct >= th.t3:
        return 3
    if success_pct >= th.t2:
        return 2
    if success_pct >= th.t1:
        return 1
    return 0


def compute_co_attainment_cie(
    questions: list[QuestionMarks],
    *,
    mode: TargetMode,
    target_pct: float,
    thresholds: AttainmentThresholds,
    policy: AttemptPolicy = AttemptPolicy.ATTEMPTED_ONLY,
) -> dict[str, COAttainment]:
    """
    Marks-weighted CO attainment from internal assessment.

      success_pct(CO) = sum_q[ passed_pct(q) * max_marks(q) ] / sum_q max_marks(q)

    Marks-weighted, NOT a flat mean across questions -- a 2-mark question must
    not count as much as a 15-mark one (AutoOBE_v3 Section 8).
    """
    by_co: dict[str, list[QuestionOutcome]] = {}
    for q in questions:
        by_co.setdefault(q.co_code, []).append(evaluate_question(q, mode, target_pct, policy))

    out: dict[str, COAttainment] = {}
    for co_code, outcomes in by_co.items():
        total = sum(o.max_marks for o in outcomes)
        if total <= 0:
            raise ValueError(f"CO {co_code}: total max marks must be > 0")
        pct = sum(o.passed_pct * o.max_marks for o in outcomes) / total
        out[co_code] = COAttainment(co_code, pct, level_for(pct, thresholds), outcomes)
    return out


def compute_co_attainment_see(
    *,
    students_meeting_target: int,
    students_appeared: int,
    co_codes: list[str],
    thresholds: AttainmentThresholds,
) -> dict[str, COAttainment]:
    """
    External (semester-end) attainment, distributed uniformly across the
    course's COs. Universities like AKTU publish only aggregate subject
    marks -- no per-question data exists -- so a uniform distribution is the
    accepted practice, and must be stated in the methodology note.

    Deliberately NOT a per-CO inference: assigning a CO a score derived from
    a mark no student was measured on is not auditable (AutoOBE_v3 Section 8).
    """
    if students_appeared <= 0:
        raise ValueError("students_appeared must be > 0")
    if not (0 <= students_meeting_target <= students_appeared):
        raise ValueError("students_meeting_target must be between 0 and students_appeared")
    pct = students_meeting_target / students_appeared * 100.0
    lvl = level_for(pct, thresholds)
    return {co: COAttainment(co, pct, lvl, []) for co in co_codes}


def blend_direct(level_cie: float, level_see: float, *, w_cie: float, w_see: float) -> float:
    if abs((w_cie + w_see) - 1.0) > 1e-9:
        raise ValueError(f"w_cie + w_see must equal 1.0, got {w_cie + w_see}")
    return w_cie * level_cie + w_see * level_see


def blend_overall(direct: float, indirect: float, *, w_direct: float, w_indirect: float) -> float:
    if abs((w_direct + w_indirect) - 1.0) > 1e-9:
        raise ValueError(f"w_direct + w_indirect must equal 1.0, got {w_direct + w_indirect}")
    return w_direct * direct + w_indirect * indirect


def compute_po_attainment_for_course(
    co_attainment: dict[str, float],
    co_po_weights: dict[str, dict[str, int]],
    po_codes: list[str],
) -> dict[str, float | None]:
    """
    Weighted roll-up of CO attainment into per-course PO attainment:

      PO_course(j) = sum_i[ overall(CO_i) * weight(CO_i, PO_j) ]
                     / sum_i weight(CO_i, PO_j)

    Returns None for a PO no CO addresses -- distinct from 0.0, which would
    wrongly read as "addressed and attained nothing" in Table 3.8.1.
    """
    result: dict[str, float | None] = {}
    for po in po_codes:
        num = den = 0.0
        for co, level in co_attainment.items():
            w = co_po_weights.get(co, {}).get(po, 0)
            if w > 0:
                num += level * w
                den += w
        result[po] = (num / den) if den > 0 else None
    return result


def compute_po_direct_attainment(
    per_course_po: dict[str, dict[str, float | None]],
    po_codes: list[str],
) -> dict[str, float | None]:
    """
    Program-level direct PO attainment: the plain average across all courses
    addressing that PO -- SAR 3.8.1's stated method, verbatim.

    Deliberately NOT credit-weighted. Credit-weighting is defensible but is a
    documented deviation requiring a declared policy note, so it is an
    explicit opt-in elsewhere, never the default (AutoOBE_v3 Section 8).
    """
    out: dict[str, float | None] = {}
    for po in po_codes:
        vals = [
            course_po[po]
            for course_po in per_course_po.values()
            if course_po.get(po) is not None
        ]
        out[po] = (sum(vals) / len(vals)) if vals else None  # type: ignore[arg-type]
    return out


@dataclass(frozen=True)
class ChoiceBias:
    """How much an optional-question CO's attainment depends on the policy."""
    co_code: str
    attempted_only_pct: float
    cohort_pct: float
    attempt_rate: float
    all_evidence_optional: bool

    @property
    def gap(self) -> float:
        return round(self.attempted_only_pct - self.cohort_pct, 2)

    @property
    def severity(self) -> str:
        # A CO measured only on optional questions is the case that actually
        # misleads: the skippers are disproportionately the students who could
        # not do it, so the figure flatters the cohort.
        if self.all_evidence_optional and self.gap >= 15:
            return "error"
        if self.gap >= 15 or (self.all_evidence_optional and self.gap >= 5):
            return "warning"
        return "info"

    @property
    def message(self) -> str:
        if self.gap < 1:
            return (f"{self.co_code}: almost every student attempted the optional "
                    f"questions, so the choice makes no material difference.")
        base = (
            f"{self.co_code}: {self.attempt_rate:.0%} of students attempted the "
            f"optional question(s) behind this outcome. Attainment is "
            f"{self.attempted_only_pct:.1f}% counting attempters only, but "
            f"{self.cohort_pct:.1f}% counting everyone who sat the paper — a "
            f"{self.gap:.1f} point spread."
        )
        if self.all_evidence_optional:
            base += (
                " Every question evidencing this outcome was skippable, so the "
                "higher figure is measured on a self-selected group and reads "
                "high. Either make one question compulsory or declare the "
                "cohort policy."
            )
        return base


def choice_bias_report(
    questions: list[QuestionMarks],
    *,
    mode: TargetMode,
    target_pct: float,
    thresholds: AttainmentThresholds,
) -> list[ChoiceBias]:
    """
    Compute each affected CO under both policies and report the spread.

    Only COs with at least one skippable question appear. A paper with no
    choice returns an empty list, which is why this can run unconditionally.
    """
    if not any(q.not_attempted for q in questions):
        return []

    lenient = compute_co_attainment_cie(
        questions, mode=mode, target_pct=target_pct, thresholds=thresholds,
        policy=AttemptPolicy.ATTEMPTED_ONLY,
    )
    strict = compute_co_attainment_cie(
        questions, mode=mode, target_pct=target_pct, thresholds=thresholds,
        policy=AttemptPolicy.COHORT,
    )

    out: list[ChoiceBias] = []
    for co_code, lco in lenient.items():
        qs = [q for q in questions if q.co_code == co_code]
        if not any(q.not_attempted for q in qs):
            continue
        attempted = sum(len(q.student_marks) for q in qs)
        skipped = sum(len(q.not_attempted) for q in qs)
        total = attempted + skipped
        out.append(ChoiceBias(
            co_code=co_code,
            attempted_only_pct=round(lco.success_pct, 2),
            cohort_pct=round(strict[co_code].success_pct, 2),
            attempt_rate=(attempted / total) if total else 1.0,
            all_evidence_optional=all(q.not_attempted for q in qs),
        ))
    out.sort(key=lambda c: -c.gap)
    return out
