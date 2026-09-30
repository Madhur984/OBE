"""
Indirect assessment: course-exit, alumni and employer surveys.

NBA wants both direct and indirect attainment, and until now this codebase
could only accept an indirect figure someone had computed elsewhere -- which
is exactly the unauditable step the product exists to remove. This module
closes it: it generates the instrument from the outcomes, ingests responses,
and computes the indirect number with the same thresholds the direct path
uses.

Three decisions, all of which a peer team can and does query:

**The scale conversion.** A Likert response of 4 on a 5-point scale becomes
80%, and that percentage goes through the *same* `AttainmentThresholds` as
direct attainment. The alternative -- mapping mean ratings straight onto 1/2/3
by a separate ladder -- is common in the field but means the same word
("level 2") denotes two different things in one table. One ladder, stated
once, is defensible; two is a finding waiting to happen.

**Point 1 is not zero.** A 5-point scale runs 1..5, so the worst response is
20%, not 0%. Treating it as zero would silently deflate every indirect number.

**Response rate is part of the result, not a footnote.** Eleven responses from
a class of 120 is not evidence of anything, and a system that prints "PO3:
2.8" from it is lending false authority to noise. `SurveyResult.adequacy`
says so in the result object, and the SAR note says so in words.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.engine.attainment import AttainmentThresholds, level_for

# Below these the result is reported but marked inadequate. The 25% figure is
# an institutional convention, not an NBA rule -- it is the point below which
# self-selection (only the enthusiastic and the aggrieved reply) dominates.
MIN_RESPONSE_RATE = 0.25
MIN_RESPONSES = 10

SURVEY_KINDS = ("course_exit", "alumni", "employer", "student")


@dataclass(frozen=True)
class SurveyItem:
    """One Likert statement, bound to the outcome it measures."""
    id: str
    target_kind: str          # 'CO' | 'PO' | 'PSO'
    target_code: str
    text: str

    def to_dict(self) -> dict:
        return {"id": self.id, "target_kind": self.target_kind,
                "target_code": self.target_code, "text": self.text}


@dataclass(frozen=True)
class Instrument:
    kind: str
    scale_max: int
    items: list[SurveyItem]
    preamble: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "scale_max": self.scale_max,
                "preamble": self.preamble,
                "items": [i.to_dict() for i in self.items]}


_SCALE_LABELS = {
    5: ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"],
    4: ["Strongly disagree", "Disagree", "Agree", "Strongly agree"],
    3: ["Disagree", "Neutral", "Agree"],
}


def scale_labels(scale_max: int) -> list[str]:
    return _SCALE_LABELS.get(scale_max, [str(i) for i in range(1, scale_max + 1)])


def _student_facing(statement: str) -> str:
    """
    Turn an outcome written for accreditors into a sentence a student can
    answer honestly. COs are third-person and clinical ("Analyze transaction
    schedules for serializability"); a survey item has to be first-person and
    plain, or students agree with everything to get out of the room.
    """
    s = statement.strip().rstrip(".")
    if not s:
        return "I achieved this outcome."
    lowered = s[0].lower() + s[1:]
    return f"I can {lowered}."


def build_course_exit_survey(
    cos: dict[str, str],
    *,
    scale_max: int = 5,
    course_title: str = "",
) -> Instrument:
    """
    One item per CO, generated from the outcome text. Faculty never author
    this survey -- same contract as every other screen.
    """
    items = [
        SurveyItem(id=f"item_{code}", target_kind="CO", target_code=code,
                   text=_student_facing(statement))
        for code, statement in cos.items()
    ]
    title = f" for {course_title}" if course_title else ""
    return Instrument(
        kind="course_exit",
        scale_max=scale_max,
        items=items,
        preamble=(
            f"Course exit survey{title}. For each statement, choose the option "
            f"closest to your own view. Responses are anonymous and are used to "
            f"improve the course, not to assess you individually."
        ),
    )


def build_outcome_survey(
    outcomes: dict[str, str],
    *,
    kind: str,
    target_kind: str = "PO",
    scale_max: int = 5,
) -> Instrument:
    """Alumni/employer instrument, one item per PO or PSO."""
    if kind not in SURVEY_KINDS:
        raise ValueError(f"Unknown survey kind {kind!r}; expected one of {SURVEY_KINDS}")
    if kind == "employer":
        stem = "Our engineers from this program can {}."
        preamble = ("Employer survey. Rate the graduates of this program you have "
                    "worked with, on each ability below.")
    else:
        stem = "My program prepared me to {}."
        preamble = ("Alumni survey. Looking back at your program, rate how well it "
                    "prepared you for each ability below.")
    items = []
    for code, statement in outcomes.items():
        body = statement.strip().rstrip(".")
        body = body[0].lower() + body[1:] if body else "achieve this outcome"
        items.append(SurveyItem(id=f"item_{code}", target_kind=target_kind,
                                target_code=code, text=stem.format(body)))
    return Instrument(kind=kind, scale_max=scale_max, items=items, preamble=preamble)


def respondent_token(survey_id: str, identifier: str) -> str:
    """
    Pseudonymise a respondent. We need to stop one student answering twice,
    and we must not be able to say who said what -- so store a salted digest,
    never the roll number. Salting with the survey id means the same student's
    tokens differ between surveys and cannot be joined up.

    Student responses are personal data under the DPDP Act 2023; this keeps
    the response set outside that definition rather than trying to secure it.
    """
    return hashlib.sha256(f"{survey_id}\x1f{identifier}".encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ItemResult:
    target_kind: str
    target_code: str
    responses: int
    mean_rating: float
    pct: float
    level: int
    distribution: dict[int, int] = field(default_factory=dict)


@dataclass
class SurveyResult:
    kind: str
    scale_max: int
    responses: int
    population: int | None
    items: list[ItemResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def response_rate(self) -> float | None:
        if not self.population:
            return None
        return self.responses / self.population

    @property
    def adequacy(self) -> str:
        """'adequate' | 'thin' | 'insufficient' -- reported, never hidden."""
        if self.responses < MIN_RESPONSES:
            return "insufficient"
        rate = self.response_rate
        if rate is not None and rate < MIN_RESPONSE_RATE:
            return "thin"
        return "adequate"

    def by_target(self) -> dict[str, float]:
        """
        {target_code: percentage}.

        NOT what the blend takes. Direct attainment reaching a SAR is a level
        on the 0-3 scale; these are percentages. Blending the two produced
        "PO1 = 12.67" from a direct 1.78 and an indirect 56.25 -- arithmetic
        that is only wrong if you know what the units are, which is exactly
        the kind of error that survives review. Use `levels()` to blend.
        """
        return {i.target_code: i.pct for i in self.items}

    def levels(self) -> dict[str, int]:
        """{target_code: level 0-3} -- commensurable with direct attainment."""
        return {i.target_code: i.level for i in self.items}


def score_survey(
    instrument: Instrument,
    responses: list[dict[str, float]],
    *,
    thresholds: AttainmentThresholds | None = None,
    population: int | None = None,
) -> SurveyResult:
    """
    Score responses into per-outcome percentages.

        pct = (mean_rating - 1) / (scale_max - 1) * 100

    Note the -1: on a 1..5 scale the floor is 1, so a room that answered 1 to
    everything scores 0%, and a room that answered 3 scores 50%. Dividing the
    raw mean by scale_max instead would report 20% for the worst possible
    result, which flatters every survey ever run.
    """
    th = thresholds or AttainmentThresholds()
    if instrument.scale_max < 2:
        raise ValueError("A Likert scale needs at least 2 points.")

    warnings: list[str] = []
    results: list[ItemResult] = []
    span = instrument.scale_max - 1

    for item in instrument.items:
        vals: list[float] = []
        dist: dict[int, int] = {}
        for r in responses:
            v = r.get(item.id)
            if v is None:
                continue
            if not (1 <= v <= instrument.scale_max):
                warnings.append(
                    f"{item.id}: response {v} is outside 1..{instrument.scale_max} "
                    f"and was discarded."
                )
                continue
            vals.append(float(v))
            dist[int(v)] = dist.get(int(v), 0) + 1

        if not vals:
            warnings.append(f"{item.target_code}: no usable responses.")
            results.append(ItemResult(item.target_kind, item.target_code, 0, 0.0, 0.0, 0, {}))
            continue

        mean = sum(vals) / len(vals)
        pct = (mean - 1) / span * 100.0
        results.append(ItemResult(
            item.target_kind, item.target_code, len(vals),
            round(mean, 2), round(pct, 2), level_for(pct, th), dist,
        ))

    n = len(responses)
    if n < MIN_RESPONSES:
        warnings.append(
            f"Only {n} response(s). Below {MIN_RESPONSES} the result is not "
            f"evidence of anything and should not be quoted in the SAR."
        )
    elif population and n / population < MIN_RESPONSE_RATE:
        warnings.append(
            f"Response rate {n}/{population} ({n / population:.0%}) is below "
            f"{MIN_RESPONSE_RATE:.0%}. Self-selection dominates at this rate — "
            f"state the rate alongside the figure."
        )

    return SurveyResult(instrument.kind, instrument.scale_max, n, population,
                        results, warnings)


def rollup_indirect_to_po(
    co_indirect: dict[str, float],
    co_po_weights: dict[str, dict[str, int]],
    po_codes: list[str],
) -> dict[str, float | None]:
    """
    Lift CO-level indirect attainment to PO level using the articulation
    matrix -- the identical weighting the direct path uses, so the two are
    commensurable when blended. Returns None for an unaddressed PO.
    """
    from app.engine.attainment import compute_po_attainment_for_course
    return compute_po_attainment_for_course(co_indirect, co_po_weights, po_codes)


@dataclass(frozen=True)
class BlendedAttainment:
    target_code: str
    direct: float | None
    indirect: float | None
    overall: float | None
    w_direct: float
    w_indirect: float
    note: str

    def to_dict(self) -> dict:
        return {
            "target_code": self.target_code, "direct": self.direct,
            "indirect": self.indirect, "overall": self.overall,
            "w_direct": self.w_direct, "w_indirect": self.w_indirect,
            "note": self.note,
        }


MAX_LEVEL = 3.0


def to_level_scale(
    values: dict[str, float | None],
    *,
    scale: str,
    thresholds: AttainmentThresholds | None = None,
) -> dict[str, float | None]:
    """
    Put survey figures on the 0-3 attainment scale before they are blended.

    `scale` is 'level' (already 0-3, passed through) or 'percent' (0-100,
    converted through the same thresholds direct attainment uses). Requiring
    the caller to name the unit is the point: the alternative is inferring it
    from the magnitude, which works until a percentage happens to be small.
    """
    if scale == "level":
        return dict(values)
    if scale != "percent":
        raise ValueError(f"scale must be 'level' or 'percent', got {scale!r}")
    th = thresholds or AttainmentThresholds()
    out: dict[str, float | None] = {}
    for code, v in values.items():
        out[code] = None if v is None else float(level_for(v, th))
    return out


def blend_attainment(
    direct: dict[str, float | None],
    indirect: dict[str, float | None],
    *,
    w_direct: float = 0.8,
    w_indirect: float = 0.2,
) -> list[BlendedAttainment]:
    """
    Combine direct and indirect per outcome.

    The 80:20 split is the most common convention in Indian engineering
    colleges, and it is a *convention* -- it is not stated in the SAR, so it
    is a configurable default that must be declared in the methodology note,
    never presented as a requirement.

    Where indirect is missing, the overall figure is the direct one and the
    note says so. Substituting zero for a survey nobody ran would be a
    fabricated number, which is the one thing this codebase must not produce.
    """
    if abs((w_direct + w_indirect) - 1.0) > 1e-9:
        raise ValueError(f"Weights must sum to 1.0, got {w_direct + w_indirect}")

    # Cheap unit check. Both sides should be on the 0-3 attainment scale; a
    # value above it almost certainly means a percentage was passed straight
    # in, which silently produces a plausible-looking but meaningless number.
    for name, side in (("direct", direct), ("indirect", indirect)):
        for code, v in side.items():
            if v is not None and v > MAX_LEVEL + 1e-9:
                raise ValueError(
                    f"{name} attainment for {code} is {v}, above the 0–{MAX_LEVEL:.0f} "
                    f"attainment scale. If this is a percentage, convert it with "
                    f"to_level_scale(..., scale='percent') first — blending a "
                    f"percentage with a level produces a number that means nothing."
                )

    out: list[BlendedAttainment] = []
    for code in sorted(set(direct) | set(indirect)):
        d = direct.get(code)
        i = indirect.get(code)
        if d is None and i is None:
            out.append(BlendedAttainment(code, None, None, None, w_direct, w_indirect,
                                         "No course addresses this outcome."))
        elif i is None:
            out.append(BlendedAttainment(code, d, None, d, w_direct, w_indirect,
                                         "Direct only — no indirect evidence collected."))
        elif d is None:
            out.append(BlendedAttainment(code, None, i, i, w_direct, w_indirect,
                                         "Indirect only — no course assesses this "
                                         "outcome directly, which is itself a finding."))
        else:
            out.append(BlendedAttainment(
                code, d, i, round(w_direct * d + w_indirect * i, 3),
                w_direct, w_indirect,
                f"{w_direct:.0%} direct + {w_indirect:.0%} indirect.",
            ))
    return out


def methodology_note(*, w_direct: float, w_indirect: float, scale_max: int,
                     result: SurveyResult | None = None) -> str:
    """The paragraph that has to appear beneath the table in the SAR."""
    lines = [
        f"Indirect attainment is measured by a {scale_max}-point Likert survey. "
        f"An item's percentage is (mean − 1) ÷ ({scale_max} − 1) × 100, so the "
        f"scale floor scores 0% and not {100 / scale_max:.0f}%.",
        f"Indirect scores are converted to attainment levels using the same "
        f"thresholds as direct attainment, so a 'level 2' means one thing "
        f"throughout this report.",
        f"Overall attainment is {w_direct:.0%} direct and {w_indirect:.0%} "
        f"indirect. This split is an institutional policy decision; NBA does "
        f"not prescribe it.",
    ]
    if result is not None:
        rate = result.response_rate
        rate_txt = f" ({rate:.0%} of {result.population})" if rate is not None else ""
        lines.append(
            f"This survey received {result.responses} responses{rate_txt}; "
            f"adequacy: {result.adequacy}."
        )
    return " ".join(lines)
