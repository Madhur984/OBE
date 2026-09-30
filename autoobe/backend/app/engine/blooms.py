"""
Bloom's Taxonomy reference data and CO/PO action-level conversion.

Source: "Basic guidelines for framing COs" (client-supplied, Aug 2026),
section 11 verb table, plus the CO-PO mapping Rule 2 in the same document:

    Considering Blooms Level 1 and 2 as action Level 1.
    Considering Blooms Level 3 and 4 as action Level 2.
    Considering Blooms Level 5 and 6 as action Level 3.

This module is a pure function library: no I/O, no database access, fully
unit-testable in isolation (see tests/test_blooms.py). It is Layer 1 of the
mapping engine described in AutoOBE_v3 Section 6.
"""
from __future__ import annotations

import re

BLOOM_LEVEL_VERBS: dict[int, tuple[str, tuple[str, ...]]] = {
    1: ("Remember", ("define", "list", "identify", "recall")),
    2: ("Understand", ("explain", "describe", "classify", "summarize")),
    3: ("Apply", ("apply", "calculate", "implement", "use")),
    4: ("Analyze", ("analyze", "compare", "differentiate", "examine")),
    5: ("Evaluate", ("evaluate", "justify", "assess", "critique")),
    6: ("Create", ("design", "develop", "formulate", "construct")),
}

# Vague verbs the CO-quality checker flags — not measurable regardless of
# the K-level someone tags them with.
VAGUE_VERBS: frozenset[str] = frozenset({
    "know", "understand", "learn", "gain knowledge", "be familiar with",
    "appreciate", "become aware of",
})

MIN_BLOOM_LEVEL = 1
MAX_BLOOM_LEVEL = 6


def bloom_level_name(level: int) -> str:
    if level not in BLOOM_LEVEL_VERBS:
        raise ValueError(f"Bloom level must be 1-6, got {level!r}")
    return BLOOM_LEVEL_VERBS[level][0]


def action_level_for_bloom(blooms_levels: list[int]) -> int:
    """
    Map a CO's Bloom's Knowledge Level(s) to an "action level" (CL, 1-3) per
    the client's Rule 2. A CO's blooms_levels is an array (a real AKTU
    syllabus CO can be tagged e.g. [3, 4] — see AutoOBE_v3 Section 3.1) —
    per the same hard-ceiling principle established in the source plan, we
    use the HIGHEST tagged level, since that is the strongest claim the CO
    makes about student capability, and the full set is retained separately
    as evidence.

    CL1: Bloom 1-2 (Remember, Understand)
    CL2: Bloom 3-4 (Apply, Analyze)
    CL3: Bloom 5-6 (Evaluate, Create)
    """
    if not blooms_levels:
        raise ValueError("A course outcome must carry at least one Bloom's level")
    highest = max(blooms_levels)
    if not (MIN_BLOOM_LEVEL <= highest <= MAX_BLOOM_LEVEL):
        raise ValueError(f"Bloom level must be 1-6, got {highest!r}")
    if highest <= 2:
        return 1
    if highest <= 4:
        return 2
    return 3


def highest_bloom_level(blooms_levels: list[int]) -> int:
    if not blooms_levels:
        raise ValueError("A course outcome must carry at least one Bloom's level")
    return max(blooms_levels)


# Verbs that open an outcome but are not in the Bloom tables. Mapped to the
# level they actually denote, so the CO is not silently floored at K2.
#
# The second block was added from evidence: these are the leading verbs in the
# college's own 2024-25 mapping sheet that no table recognised, so 214 real
# outcomes were being read at the K2 default. British spellings are the single
# biggest group -- "Analyse RLC circuits" is a K4 outcome that scored K2 purely
# because the table spells it "analyze".
_WEAK_LEADING_VERB: dict[str, int] = {
    "express": 2, "describe": 2, "acquire": 2, "study": 2, "discuss": 2,
    "outline": 2, "illustrate": 2, "comprehend": 2, "state": 2, "recognize": 1,
    "recognise": 1, "realize": 1, "realise": 1, "do": 3, "perform": 3,
    "measure": 3, "compute": 3, "obtain": 3, "gain": 2, "review": 4,
    "differentiate": 4, "interpret": 4, "examine": 4, "investigate": 4,
    "justify": 5, "assess": 5, "validate": 5, "verify": 5,
    # British spellings of verbs the tables already carry
    "analyse": 4, "summarise": 2, "optimise": 6, "utilise": 3, "organise": 3,
    # observed leading verbs, at the level the word denotes
    "distinguish": 4, "determine": 3, "utilize": 3, "represent": 2,
    "correlate": 4, "connect": 3, "discover": 4, "appraise": 5,
    "familiarize": 2, "familiarise": 2, "read": 1, "write": 3, "draw": 3,
    "sketch": 3, "plot": 3, "derive": 3, "choose": 1, "name": 1, "label": 1,
    "predict": 4, "relate": 2, "deduce": 4, "operate": 3, "handle": 3,
}

# An adverb is never the action: "Effectively write the report" is a K3
# outcome that stopped the scanner dead on "effectively".
#
# A closed list, not a `[a-z]+ly` pattern. That pattern also strips **Apply**
# -- the single most common verb in an AKTU course outcome -- which floored
# every "Apply the concept of..." outcome at the K2 default and cost 4.4
# points of agreement against the college's own sheet before it was caught.
_LEADING_ADVERB = re.compile(
    r"^\s*(?:effectively|efficiently|practically|clearly|correctly|properly|"
    r"successfully|independently|critically|accurately|appropriately|"
    r"systematically|logically|confidently)\s+", re.I)

# "Students will be able to design CMOS circuits" opens with a preamble, not
# with an action. The scanner below stops at the first word that is not a
# verb, so without this it stopped on "students", found nothing, and fell back
# to the K2 default -- reading a K6 design outcome as CL 1. That was 45 of the
# 214 outcomes in the college's own 2024-25 mapping sheet, 17 of them landing
# on the wrong action level. Stripping the preamble restores the intended
# behaviour rather than widening the scan: what follows it IS the leading verb.
_STUDENT_PREAMBLE = re.compile(
    r"^\s*(?:(?:the\s+)?students?\s+(?:will|shall|should|can|must|are\s+expected\s+to)?\s*"
    r"(?:be\s+able\s+to|demonstrate\s+the\s+ability\s+to|have\s+the\s+ability\s+to)?\s*"
    r"|(?:at\s+the\s+end\s+of\s+(?:the\s+)?course[, ]+)"
    r"|(?:on\s+completion\s+of\s+(?:the\s+)?course[, ]+))+", re.I)


def leading_action_levels(statement: str) -> list[int]:
    """
    Bloom levels of the verb(s) that *open* the outcome.

    Only the leading verb phrase counts. Scanning further into the sentence
    reads subject matter as the action: "Express the concept of VLSI design"
    was inferred K6 because "design" appears as the sixth word, which then
    mapped a plain explain-level outcome onto the design PO at level 3 —
    precisely the spurious weight-3 the whole engine is calibrated to avoid.

    Conjoined leading verbs all count: "Describe, analyze and design simple
    microwave circuits" genuinely claims K2, K4 and K6, and the action level
    takes the highest.

    But only *conjoined* ones. A second verb-looking word sitting directly
    against the first is a noun modifying the object, not a second action:
    "Communicate design decisions" read K3 and K6, and the K6 mapped a
    reporting outcome onto the design PO at level 3. Real conjoined verbs
    always carry a separator -- "Describe, analyze and design" -- so the
    separator is what is required. This is the same mistake PO4's action
    level came from ("Use research methods including *design of experiments*")
    and it is worth fixing in one place rather than per statement.

    Returns [] when the statement opens with no recognisable verb at all, so
    the caller can decide rather than being handed a fabricated level.
    """
    from app.engine.co_quality import GOOD_VERBS       # noqa: PLC0415 -- cycle

    head = _LEADING_ADVERB.sub("", _STUDENT_PREAMBLE.sub("", statement or ""))
    # Commas are kept as tokens: they are half of what separates a genuine
    # list of verbs from a compound noun, and stripping them made
    # "Describe, analyze and design" indistinguishable from "Communicate
    # design decisions".
    words = re.findall(r"[a-z]+|,", head.lower())
    if not words:
        return []
    i = 1 if words[0] == "to" else 0
    levels: list[int] = []
    separated = True                   # the opening position needs no separator
    while i < len(words):
        w = words[i]
        if w in (",", "and", "or", "&"):
            if not levels:
                break                  # a separator before any verb is not a list
            separated = True
            i += 1
            continue
        level = GOOD_VERBS.get(w, _WEAK_LEADING_VERB.get(w))
        if level is None or not separated:
            break
        levels.append(level)
        separated = False
        i += 1
    return sorted(set(levels))


# A second action joined by a conjunction: "Analyze various logic families
# AND DESIGN circuits using PLDs" claims K4 and K6, and the department's own
# matrix maps it at the design level. The leading-phrase scan above cannot
# see it, because "various logic families" stands in between.
#
# The trap this must not fall into is the one `leading_action_levels`
# documents: reading subject matter as an action. "Demonstrate the working
# principle AND DESIGN OF bipolar junction transistor" is a noun. The first
# guard is the preposition -- a verb followed directly by "of" is a
# nominalisation, not an action. Across 262 real outcomes that single guard
# separates the one false case from the other 58 genuine ones.
#
# The second guard is a closed list of the nouns these documents put after
# "design" and "analysis" to form a compound: "write effective reports and
# DESIGN DOCUMENTATION" is one noun phrase, and reading it as an action put
# K6 on the communication PO -- which is exactly why that PO's action level
# was 1 instead of 2. This is a lexicon, not a parser, and it stays a closed
# list on purpose: "and design circuits", "and design a system", "and design
# various ADCs" are all genuine actions and none of them appear here.
_COMPOUND_NOUN_HEAD = (
    "documentation|decisions?|considerations?|specifications?|principles?|"
    "process(?:es)?|methodolog(?:y|ies)|rules?|constraints?|criteria|"
    "requirements?|flow|tools?|parameters?|aspects?|issues?|techniques?|"
    "cycle|phase|stage|steps?|patterns?|guidelines?"
)
# `(?:and\s+)?` after the comma: "Identify, formulate, review research
# literature, AND ANALYZE complex engineering problems" lost its culminating
# verb, because the comma matched and consumed "and" as the word. That verb
# is the whole of PO2 -- the PO is literally titled Problem Analysis.
_SECOND_PREDICATE = re.compile(
    r"(?:\band\b|&|,)\s+(?:and\s+)?([a-z]+)\b"
    rf"(?!\s+(?:of|{_COMPOUND_NOUN_HEAD})\b)", re.I)


def conjoined_action_levels(statement: str) -> list[int]:
    """Bloom levels of further actions the outcome joins on with "and"."""
    from app.engine.co_quality import GOOD_VERBS       # noqa: PLC0415 -- cycle

    levels: list[int] = []
    for m in _SECOND_PREDICATE.finditer(_STUDENT_PREAMBLE.sub("", statement or "")):
        word = m.group(1).lower()
        if word in GOOD_VERBS:
            levels.append(GOOD_VERBS[word])
        elif word in _WEAK_LEADING_VERB:
            levels.append(_WEAK_LEADING_VERB[word])
    return sorted(set(levels))


def infer_bloom_levels(statement: str, *, default: int = 2) -> tuple[list[int], bool]:
    """
    Best reading of a statement's Bloom level(s), and whether it is a guess.

    `default` is deliberately K2: a floor that cannot over-claim. An outcome
    wrongly floored low costs a human one correction; one wrongly raised to
    K6 produces a level-3 mapping onto the design PO that a bulk approval
    will carry straight into a SAR.
    """
    levels = leading_action_levels(statement)
    if not levels:
        return [default], True
    # A conjoined action only counts once the outcome has a leading verb. On
    # its own it is far more likely to be a noun the scan wandered into.
    return sorted(set(levels) | set(conjoined_action_levels(statement))), False
