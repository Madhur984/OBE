"""
CO quality checker -- the 8-question test, as code.

Directly implements the quality test table from "Basic guidelines for framing
COs" (client-supplied). Deterministic and explainable: no LLM in this path.
Findings are advisory and shown to faculty as suggestions -- this never
rewrites a CO on its own.

The checks that can be decided mechanically (action verb present, vague verb
absent, Bloom level identifiable, single dominant level, length/specificity)
are enforced here. The ones needing human or course-context judgement
(realistic within course duration) are returned as prompts, not verdicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.engine.blooms import BLOOM_LEVEL_VERBS, VAGUE_VERBS

_ALL_GOOD_VERBS: dict[str, int] = {
    verb: level for level, (_, verbs) in BLOOM_LEVEL_VERBS.items() for verb in verbs
}

# The guidelines say good verbs "include" their list, so it is explicitly not
# exhaustive. These are further verbs that are directly observable and
# assessable -- several of them (communicate, conduct, select) appear in NBA's
# own PO statements, and rejecting them produced false "needs work" verdicts
# on perfectly sound outcomes.
_EXTRA_VERBS: dict[str, int] = {
    "solve": 3, "demonstrate": 3, "examine": 4, "construct": 6,
    "communicate": 3, "present": 3, "prepare": 3, "select": 3, "conduct": 3,
    "perform": 3, "estimate": 3, "model": 3, "simulate": 3, "test": 3,
    "measure": 3, "calculate": 3, "compute": 3, "implement": 3,
    "interpret": 4, "investigate": 4, "validate": 5, "optimize": 6,
    "optimise": 6, "create": 6, "build": 6, "collaborate": 3,
}
GOOD_VERBS: dict[str, int] = {**_ALL_GOOD_VERBS, **_EXTRA_VERBS}

_TEACHER_INTENT = re.compile(
    r"^\s*(to\s+(provide|give|introduce|teach|impart|familiarize))|"
    r"^\s*(student[s]?\s+(should|will)\s+be\s+(aware|able))",
    re.IGNORECASE,
)


class Severity(str, Enum):
    ERROR = "error"      # CO is not usable for attainment as written
    WARNING = "warning"  # usable but weak; flag for review
    INFO = "info"        # prompt for human judgement


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class COQualityReport:
    co_code: str
    statement: str
    findings: list[Finding]
    detected_verb: str | None
    detected_bloom_level: int | None

    @property
    def is_usable(self) -> bool:
        return not any(f.severity is Severity.ERROR for f in self.findings)


def _first_verb(statement: str) -> tuple[str | None, int | None]:
    words = re.findall(r"[a-z]+", statement.lower())
    for w in words[:6]:
        if w in GOOD_VERBS:
            return w, GOOD_VERBS[w]
    return None, None


def check_co(
    co_code: str,
    statement: str,
    *,
    declared_blooms_levels: list[int] | None = None,
) -> COQualityReport:
    findings: list[Finding] = []
    text = statement.strip()
    lowered = text.lower()

    if not text:
        return COQualityReport(
            co_code, statement,
            [Finding("Action", Severity.ERROR, "The course outcome is empty.")],
            None, None,
        )

    # Test 1 -- Action: does it begin with a measurable action verb?
    verb, verb_level = _first_verb(text)
    if verb is None:
        findings.append(Finding(
            "Action", Severity.ERROR,
            "No measurable action verb found near the start. Begin with a verb "
            "such as Apply, Analyze, Design, Evaluate, or Compare.",
        ))

    # Vague verbs -- "understand" cannot be directly measured.
    for vague in VAGUE_VERBS:
        if re.search(rf"\b{re.escape(vague)}\b", lowered):
            findings.append(Finding(
                "Measurable", Severity.ERROR,
                f"Uses the vague verb \"{vague}\", which cannot be directly "
                f"measured. Replace it with an observable action verb.",
            ))
            break

    # Teacher-intent phrasing rather than student capability.
    if _TEACHER_INTENT.search(text):
        findings.append(Finding(
            "Action", Severity.ERROR,
            "Written as teacher intent or awareness rather than demonstrable "
            "student capability. State what the student will be able to do.",
        ))

    # Test 2 -- Content: is it clear what the student acts upon?
    if len(text.split()) < 6:
        findings.append(Finding(
            "Content", Severity.WARNING,
            "Very short, so the subject matter may be unclear. Name what the "
            "student acts upon and under what conditions.",
        ))

    # Test 6 -- Specific: single dominant Bloom level.
    verbs_present = sorted({
        w for w in re.findall(r"[a-z]+", lowered) if w in GOOD_VERBS
    })
    if len(verbs_present) > 2:
        findings.append(Finding(
            "Specific", Severity.WARNING,
            f"Contains several action verbs ({', '.join(verbs_present)}), so it "
            f"likely bundles multiple outcomes. Consider splitting it -- this "
            f"also makes attainment calculation more meaningful.",
        ))

    if declared_blooms_levels:
        levels = sorted(set(declared_blooms_levels))
        if len(levels) > 1:
            findings.append(Finding(
                "Bloom", Severity.WARNING,
                f"Tagged with multiple Bloom levels {levels}. Attainment will use "
                f"the highest (K{max(levels)}); the full set is retained as "
                f"evidence. A single dominant level maps more cleanly.",
            ))
        if verb_level is not None and verb_level not in levels:
            findings.append(Finding(
                "Bloom", Severity.WARNING,
                f"The verb \"{verb}\" suggests Bloom level K{verb_level}, but the "
                f"outcome is tagged {levels}. Confirm which is intended.",
            ))
    elif verb_level is None:
        findings.append(Finding(
            "Bloom", Severity.ERROR,
            "No Bloom's level could be identified and none was declared.",
        ))

    # Test 7 -- Realistic: needs course context, so prompt rather than judge.
    findings.append(Finding(
        "Realistic", Severity.INFO,
        "Confirm this is achievable within the course's contact hours, syllabus, "
        "and student entry level.",
    ))

    return COQualityReport(co_code, text, findings, verb, verb_level)
