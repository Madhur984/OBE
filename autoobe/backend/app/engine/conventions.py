"""
Departmental conventions — decisions the department made, kept off the
inference path.

A convention is not something the engine read in an outcome's text. It is a
decision the department made about its own programme, and the two must never
be confused in a document a peer team reads. In the department's own
2024-25 matrix the life-long learning column is the most heavily populated of
all fourteen — 191 of 214 outcomes at level 3, 92.1% coverage — because the
department holds that every course contributes to it. No amount of reading
"Recognize the need for ... independent and lifelong learning" against a
course outcome about CMOS layout will produce that, and it would be dishonest
to tune the scorer until it did.

So the convention is applied here instead, under four rules that are the
whole point of this module existing:

  1. **It sets competency, never a level.** The mapping level still comes
     from the untouched Rule 1 / Rule 2 in `mapping_rules.py`. That is why
     the numbers work out: the life-long learning PO carries PL 1 in both
     frameworks, so `CL >= PL` holds for every Bloom level and Rule 2 returns
     3 — the department's own column, reached by *feeding* the rules rather
     than bypassing them.

  2. **By default it only fills a cell the engine left empty.** The life-long
     learning convention never does anything else. The core-columns
     convention accepts `on_conflict="raise"`, which lets the department's
     assertion beat a *weaker* reading of the text — and only a weaker one;
     lowering an inference is refused outright. Reproducing a hand-built
     matrix needs this, because a scorer's "partial" is its confidence in a
     lexical match rather than a judgement that the course only partially
     teaches PO1. Every raised cell carries `raised_from`, so the count is
     never buried in an aggregate.

  3. **It is reported separately.** Convention cells come back in their own
     list, carry the reason they exist, and are never merged into the measured
     `competency` the engine proposed. Nothing here touches the accuracy
     evaluation, which keeps measuring inference alone.

     They are deliberately NOT marked in the matrix or against the exported
     columns. The department asked for their own sheet reproduced, and half
     its columns flagged with a dagger is an annotation on their document
     rather than a copy of it -- it also reads as a defect where none exists,
     since the department mapping its own core columns is a decision it is
     entitled to make. What the columns are is stated once, in the method
     note that already explains the two averaging conventions, and each cell
     names its own source when clicked.

  4. **It is off unless asked for.** The server default is `enabled=False`;
     the page ships the checkbox on, so a request that reaches this module
     from the UI has that stated in it. Both defaults are written down here
     together so that reading either one alone cannot mislead.

`competency.py`, `mapping_rules.py` and `copo_eval.py` must never import this
module, and it must never import them. The measured accuracy figures have to
stay measurements of what the engine infers.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.engine.frameworks import lifelong_po_code

LIFELONG = "lifelong"
CORE = "core"
LIFELONG_LABEL = "departmental convention"

# The columns a department asserts for every technical course. Measured from
# the department's own 2024-25 matrix, where each of PO1, PO2, PO3, PO4, PO5
# and both PSOs is mapped for 85% of all 214 outcomes and for every outcome
# of every engineering course -- while PO6..PO11 sit between 10% and 19% and
# are plainly judged case by case.
#
# Presence is all this asserts. Which LEVEL each cell gets is still Rule 2's
# answer from that outcome's own Bloom level, and the difference is visible:
# in their Digital System Design block the four design outcomes map PO3 at 3
# and the one "Perform … simplification" outcome maps it at 2, because CL 2
# is below PO3's PL 3. Reproducing that distinction is the point.
CORE_COLUMNS: tuple[str, ...] = ("PO1", "PO2", "PO3", "PO4", "PO5")

# ---- which columns a course of a given kind contributes to --------------
#
# "Map it the way a human does it." A person building this matrix does not
# give every course the same columns: a lecture course and a course on the
# Constitution of India contribute to different halves of the framework, and
# a project contributes to communication in a way a lecture does not.
#
# These four shapes are not invented -- they are read out of the department's
# own 2024-25 sheet, where the split is total rather than a tendency:
#
#   theory   (100 outcomes)  PO1-PO5 on 100% of rows, PO6-PO11 on 0%
#   lab       (64 outcomes)  identical shape, and stronger within it
#   project   (25 outcomes)  PO1-PO5 plus PO10 on 76% -- the report and viva
#   non-tech  (25 outcomes)  PO6-PO11 only, and PO1-PO5 on exactly 0 rows
#
# The six non-technical courses are Universal Human Values, Technical
# Communication, Constitution of India, Indian Tradition Culture and Society,
# Project Management and Entrepreneurship, and Rural Development. Handing
# those PO1-PO5 -- which one setting for a whole workbook does -- puts
# engineering knowledge against a course on rural development, which is the
# kind of cell a peer team opens the file to find.
THEORY = "theory"
LAB = "lab"
PROJECT = "project"
NON_TECHNICAL = "non-technical"

COURSE_COLUMNS: dict[str, tuple[str, ...]] = {
    THEORY: CORE_COLUMNS,
    LAB: CORE_COLUMNS,
    PROJECT: CORE_COLUMNS + ("PO10",),
    NON_TECHNICAL: ("PO6", "PO7", "PO8", "PO9", "PO10", "PO11"),
}

COURSE_KIND_LABEL = {
    THEORY: "theory course",
    LAB: "laboratory course",
    PROJECT: "project or internship",
    NON_TECHNICAL: "non-technical course",
}

# Checked before the project test, because "Project Management and
# Entrepreneurship" is a management course and their sheet maps it PO6-PO11.
_NON_TECHNICAL_TITLE = re.compile(
    r"universal\s+human\s+value|human\s+value|professional\s+ethic|\buhv\b|"
    r"technical\s+communication|communication\s+skill|soft\s+skill|"
    r"constitution\s+of\s+india|indian\s+tradition|culture\s+and\s+society|"
    r"project\s+management|entrepreneur|rural\s+development|"
    r"sports\s+and\s+yoga|\bnss\b|disaster\s+management|"
    r"industrial\s+(?:management|sociology)|business\s+communication", re.I)
# AKTU category letters in the middle of a course code. VE = value education,
# NC = non-credit, HU = humanities. AS is deliberately absent: BAS/KAS carries
# Mathematics, which their sheet maps as a technical course.
_NON_TECHNICAL_CODE = re.compile(r"^[A-Z]?(?:VE|NC|HU)\s*-?\s*\d", re.I)
# A title that reads non-technical but names a technical subject. Checked
# BEFORE the non-technical test, because "project management" is in that
# pattern: AKTU's BOE068 and KIT071 Software Project Management were being
# filed alongside Rural Development and handed the PO6-PO11 column set. It is
# a computer-science elective -- estimation models, risk analysis,
# configuration-management tools -- and PO1-PO5 are where it belongs.
#
# The qualifier carries it. Bare "Project Management" (the department's own
# KHU-702, AKTU's MNPM401) stays non-technical; a named engineering discipline
# in front of it does not.
_TECHNICAL_DESPITE_TITLE = re.compile(
    r"\b(?:software|construction|civil|mechanical|electrical|chemical|"
    r"industrial|manufacturing|it|engineering)\s+project\s+management\b", re.I)
_LAB_TITLE = re.compile(r"\blab(?:oratory)?\b|\bworkshop\b|\bpractical\b", re.I)
_PROJECT_TITLE = re.compile(
    r"\bmini\s*project\b|\bproject\b(?!\s+management)|\binternship\b|"
    r"\bdissertation\b|\bseminar\b|\bindustrial\s+training\b", re.I)


def classify_course(*, title: str = "", code: str = "",
                    ltp: tuple[int, int, int] | list[int] | None = None) -> str:
    """
    Which of the four shapes this course has, from what a syllabus carries.

    Title first, because it is the thing a human reads. The L-T-P triple is
    the tiebreak that catches a lab whose title does not say "Lab": zero
    lecture hours with practical hours is a laboratory course whatever it is
    called. Nothing here guesses beyond those two, and the answer is reported
    to the user rather than applied silently.
    """
    t = (title or "").strip()
    c = (code or "").strip()
    if not _TECHNICAL_DESPITE_TITLE.search(t) and (
            _NON_TECHNICAL_TITLE.search(t) or _NON_TECHNICAL_CODE.match(c)):
        return NON_TECHNICAL
    if _LAB_TITLE.search(t):
        return LAB
    if _PROJECT_TITLE.search(t):
        return PROJECT
    if ltp:
        try:
            lec, _tut, prac = (int(x) for x in tuple(ltp)[:3])
        except (TypeError, ValueError):
            return THEORY
        if lec == 0 and prac > 0:
            return LAB
    return THEORY


def columns_for_course(*, title: str = "", code: str = "",
                       ltp=None, pso_codes: tuple[str, ...] = ()) -> tuple[str, tuple[str, ...]]:
    """
    Returns (kind, columns). The PSOs join a technical course and not a
    non-technical one, which is again the department's own practice: their six
    non-technical blocks leave both PSO columns empty on every row.
    """
    kind = classify_course(title=title, code=code, ltp=ltp)
    cols = COURSE_COLUMNS[kind]
    if kind != NON_TECHNICAL:
        cols = cols + tuple(pso_codes)
    return kind, cols


def withhold_core_columns(
    competency: Mapping[str, Mapping[str, int | str]], *, kind: str,
) -> tuple[dict[str, dict[str, int | str]], list[tuple[str, str]]]:
    """
    Take the engineering columns off a non-technical course, and say which.

    This is the one place a *convention* overrides a reading rather than only
    filling a gap, and it needs the argument spelled out.

    The scorer reads one sentence at a time and has no idea what course it
    belongs to. "Differentiate and relate the functioning of Indian
    parliamentary system at the centre and state level" contains
    "differentiate", which is a Problem Analysis verb wherever it appears, so
    the sentence proposes PO2 at competency 2 -- on a course about the
    Constitution of India. The department's own sheet maps PO1-PO5 on **zero**
    of the 25 outcomes across their six non-technical courses, and a peer team
    opening the file to find Problem Analysis against Constitutional Law is
    the exact failure this whole module exists to prevent.

    So the course kind wins over the sentence. It is applied only where the
    kind is known and reported, never silently: every withheld cell comes back
    in the list so the UI can name the count and a user who disagrees can
    reclassify the course.

    The reverse is deliberately NOT done. A technical course is left free to
    reach PO6-PO11 if its text earns it -- an Environmental Engineering course
    genuinely does, and their sheet's 0% there is a fact about ECE syllabi
    rather than a rule about engineering.
    """
    out = {co: dict(row) for co, row in competency.items()}
    if kind != NON_TECHNICAL:
        return out, []
    withheld: list[tuple[str, str]] = []
    for co, row in out.items():
        for po in CORE_COLUMNS:
            if row.pop(po, None) not in (None, 0, "0", "none"):
                withheld.append((co, po))
    return out, withheld


@dataclass(frozen=True)
class ConventionConfig:
    # Server default: nothing is applied unless a request asks for it.
    # The "/" page ships this checkbox ON -- see the module docstring.
    enabled: bool = False
    competency: int = 2             # 0 off, 1 partial, 2 strong
    exempt_cos: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.competency not in (0, 1, 2):
            raise ValueError(
                f"convention competency must be 0, 1 or 2, got {self.competency!r}")


@dataclass(frozen=True)
class ConventionCell:
    co_code: str
    po_code: str
    competency: int                 # 1 or 2 -- never a level
    convention: str
    reason: str
    # Set when the convention overrode a weaker reading the engine had made,
    # so the count of overridden cells is never hidden in an aggregate.
    raised_from: int | None = None
    # Set on a core column the text could not evidence, filled at the floor
    # rather than left blank. Reported for the same reason as `raised_from`:
    # a 1 the department's curriculum argument put there is not the same cell
    # as a 1 the outcome earned, and the matrix should never blur the two.
    floored: bool = False

    def to_dict(self) -> dict:
        return {
            "co_code": self.co_code, "po_code": self.po_code,
            "competency": self.competency, "convention": self.convention,
            "reason": self.reason, "label": LIFELONG_LABEL,
            "raised_from": self.raised_from, "floored": self.floored,
        }


@dataclass
class ConventionResult:
    competency: dict[str, dict[str, int]]
    cells: list[ConventionCell] = field(default_factory=list)

    @property
    def codes(self) -> tuple[str, ...]:
        """The PO codes this convention touched, for marking a header."""
        return tuple(dict.fromkeys(c.po_code for c in self.cells))

    def note(self, framework_code: str = "") -> str:
        if not self.cells:
            return ""
        po = self.codes[0]
        return (
            f"{po} is filled by departmental convention, not "
            f"by inference from the outcome text: the department holds that "
            f"every course contributes to life-long learning. {len(self.cells)} "
            f"cell(s) were added this way, and only where the engine proposed "
            f"nothing. The mapping level in each is still derived by Rule 1 "
            f"and Rule 2 from the outcome's own Bloom's level."
        )


def apply_convention(
    competency: Mapping[str, Mapping[str, int]],
    *,
    co_blooms: Mapping[str, list[int]],
    framework_code: str,
    config: ConventionConfig | None = None,
) -> ConventionResult:
    """
    Add the life-long learning competency the department asserts, to the COs
    that have none, and report exactly what was added.

    Never mutates its input.
    """
    config = config or ConventionConfig()
    merged = {co: dict(row) for co, row in competency.items()}

    if not config.enabled or config.competency == 0:
        return ConventionResult(merged, [])

    po = lifelong_po_code(framework_code)
    if po is None:
        # A framework with no life-long learning PO is not an error; there is
        # simply nothing to apply. Degrading silently beats refusing a matrix.
        return ConventionResult(merged, [])

    cells: list[ConventionCell] = []
    for co_code, blooms in co_blooms.items():
        if co_code in config.exempt_cos:
            continue
        existing = int((competency.get(co_code) or {}).get(po, 0) or 0)
        if existing > 0:
            continue                # rule 2: never overwrite an inference

        want = config.competency
        reason = (
            f"Every course contributes to life-long learning, so the "
            f"department maps {po} for every outcome."
        )
        if want == 2 and not blooms:
            # Strong competency with no Bloom's level cannot be levelled by
            # Rule 2, and the mapping endpoint refuses it. A convention must
            # never be the thing that breaks a matrix, so it steps down.
            want = 1
            reason += (
                " Recorded as a partial match here because this outcome "
                "carries no Bloom's level for Rule 2 to use."
            )

        merged.setdefault(co_code, {})[po] = want
        cells.append(ConventionCell(co_code, po, want, LIFELONG, reason))

    return ConventionResult(merged, cells)


@dataclass(frozen=True)
class CoreConfig:
    """The columns a department asserts for every outcome of a technical course."""
    enabled: bool = False           # server default: never applied unless asked
    codes: tuple[str, ...] = CORE_COLUMNS
    competency: int = 2
    exempt_cos: tuple[str, ...] = ()
    # What to do when the engine already read *weaker* evidence in the text
    # than the department asserts for the column.
    #
    # "keep" leaves the engine's reading alone, which is the safe default and
    # the right one for a column the department has said nothing about.
    #
    # "raise" lets the assertion win, and is what reproduces a hand-built
    # matrix: a scorer's "partial" is its confidence in a lexical match, not a
    # considered judgement that the course only partially teaches PO1, and the
    # convention is precisely the department saying that the scorer is not the
    # instrument for these columns. Never silent -- every raised cell is
    # returned with `raised_from` set, so the count is auditable and the UI
    # can show which cells moved.
    #
    # It can only ever raise. Lowering an inference is refused outright.
    on_conflict: str = "keep"       # keep | raise
    # Fill a named column only where the outcome (or the course syllabus
    # behind it) shows some trace of that PO, instead of every column for
    # every outcome.
    #
    # The department's instruction, verbatim: "it is not necessary to fill all
    # the columns till PO5 -- if they really needed to be filled then only
    # fill them, else let them be as they are." Without this the convention is
    # a rubber stamp: five columns times every outcome, the same five numbers
    # down every block, which is the pattern a peer team is trained to
    # discount. With it the convention still says "this department maps these
    # columns", but each cell has to be able to point at a word.
    #
    # The evidence itself is computed by the caller and passed in, so this
    # module stays off the inference path -- see `competency.signal_strength`.
    require_evidence: bool = False
    # What an unevidenced *core* column gets on a technical course.
    #
    # "blank" leaves it out, which is what `require_evidence` alone does.
    #
    # "partial" fills it at competency 1 instead. The department's second
    # instruction, verbatim: "for technical subjects make sure to fill PO1-PO5;
    # if according to you they are least relatable then keep them 1, but fill
    # them." Their reasoning is a curriculum one rather than a textual one --
    # an engineering course contributes *something* to engineering knowledge,
    # problem analysis, design, investigation and tool usage even when its five
    # outcome sentences never say so, and a blank there reads to a peer team as
    # "this course does not contribute", which is a stronger claim than the
    # department wants to make. A 1 says the honest thing: it contributes, but
    # not enough for this outcome to carry it.
    #
    # This is deliberately narrower than `codes`. It applies only to the five
    # core columns, never to PO10 on a project block -- the department's own
    # sheet fills that on roughly three rows in four, so a floor there would
    # invent a pattern they do not have.
    unevidenced: str = "blank"      # blank | partial

    def __post_init__(self) -> None:
        if self.competency not in (0, 1, 2):
            raise ValueError(
                f"convention competency must be 0, 1 or 2, got {self.competency!r}")
        if self.on_conflict not in ("keep", "raise"):
            raise ValueError(
                f"on_conflict must be 'keep' or 'raise', got {self.on_conflict!r}")
        if self.unevidenced not in ("blank", "partial"):
            raise ValueError(
                f"unevidenced must be 'blank' or 'partial', got {self.unevidenced!r}")


def apply_core_columns(
    competency: Mapping[str, Mapping[str, int]],
    *,
    co_blooms: Mapping[str, list[int]],
    valid_codes: Mapping[str, int] | set[str] | tuple[str, ...],
    config: CoreConfig | None = None,
    evidence: Mapping[str, Mapping[str, float]] | None = None,
    evidence_scope: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> ConventionResult:
    """
    Assert the department's core columns for every outcome, then let the rules
    decide each level.

    Same four rules as `apply_convention`: competency only, never over an
    existing inference, reported separately, and off unless asked for. The
    difference from an inference is worth being exact about — the engine reads
    one outcome at a time and asks "does this text evidence PO3?", while the
    department is asserting a fact about the course as a whole. Both are
    legitimate; conflating them is not, which is why they arrive in different
    fields and print with a dagger.
    """
    config = config or CoreConfig()
    merged = {co: dict(row) for co, row in competency.items()}
    if not config.enabled or config.competency == 0 or not config.codes:
        return ConventionResult(merged, [])

    known = set(valid_codes)
    # The columns the caller's evidence actually covers. Anything outside it
    # is never vetoed for want of evidence -- see the note in the loop.
    scope = set(evidence_scope)
    cells: list[ConventionCell] = []
    for co_code, blooms in co_blooms.items():
        if co_code in config.exempt_cos:
            continue
        seen_here = (evidence or {}).get(co_code) or {}
        for code in config.codes:
            if code not in known:
                continue            # a column this framework does not have
            existing = int((competency.get(co_code) or {}).get(code, 0) or 0)
            want = config.competency
            unevidenced = (config.require_evidence and not existing
                           and code in scope and not seen_here.get(code))
            if unevidenced:
                # Nothing in this outcome or its course content points at this
                # PO.
                #
                # `code in scope` matters: a PSO has a statement the department
                # wrote and no signal lexicon behind it, so nothing can ever
                # evidence one. Vetoing a column the scorer cannot score would
                # be reading "no scorer" as "no evidence", which emptied both
                # PSO columns outright the first time this ran.
                if config.unevidenced != "partial" or code not in CORE_COLUMNS:
                    # Left exactly as it was -- usually blank.
                    continue
                # Filled at the floor instead. See `CoreConfig.unevidenced`.
                want = 1
            if want == 2 and not blooms:
                want = 1
            if existing >= want:
                continue            # the engine already read this or better
            if existing > 0 and config.on_conflict != "raise":
                continue            # a weaker reading stands unless asked

            reason = (
                f"The department maps {code} for every outcome of a technical "
                f"course. The level below is still Rule 2's answer from this "
                f"outcome's own Bloom's level."
            )
            if unevidenced:
                reason = (
                    f"Nothing in this outcome or its course content points at "
                    f"{code}, so it is recorded at the floor: a technical "
                    f"course contributes something here, but this outcome is "
                    f"not where it is carried."
                )
            elif want == 1 and config.competency == 2:
                reason += (
                    " Recorded as a partial match because this outcome carries "
                    "no Bloom's level for Rule 2 to use."
                )
            if existing > 0:
                reason += (
                    f" The engine read only competency {existing} in this "
                    f"outcome's text; the department's assertion about the "
                    f"course was taken instead."
                )
            merged.setdefault(co_code, {})[code] = want
            cells.append(ConventionCell(co_code, code, want, CORE, reason,
                                        raised_from=existing or None,
                                        floored=unevidenced))

    return ConventionResult(merged, cells)
