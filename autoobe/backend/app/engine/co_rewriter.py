"""
Layer 5b -- the CO statement rewriter.

AKTU's published COs are frequently unusable as written. Verbatim from a
current syllabus: "Student should be aware of techniques used for
classification and clustering." Not measurable, not student-centric, no Bloom
action verb -- yet it is what the university published, so every affiliated
college inherits it.

This rewrites such statements into compliant form deterministically, following
the client's own framing guidelines:

  1. state what the student will be able to do
  2. lead with a measurable action verb
  3. match the verb to the intended Bloom level
  4. name the subject matter acted upon
  5. keep one principal outcome per CO
 10. prefer a single dominant Bloom level

Deterministic on purpose. A model would write more fluent prose, but this runs
offline, returns the same output every time, and -- critically -- can show
exactly which rule fired. Faculty see a diff and accept or edit; the
university-published original is always retained for provenance, because an
affiliated college may legitimately sharpen a CO but must be able to show what
it started from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.engine.blooms import BLOOM_LEVEL_VERBS
from app.engine.co_quality import GOOD_VERBS, check_co

# Preferred verb per Bloom level for generated text.
_CANONICAL_VERB: dict[int, str] = {
    1: "Define", 2: "Explain", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Design",
}

# Non-compliant openers -> (replacement Bloom level, phrase to strip).
_OPENER_RULES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^\s*students?\s+(should|will|must|can)\s+be\s+able\s+to\s+", re.I), 0),
    (re.compile(r"^\s*students?\s+(should|will|must)\s+be\s+aware\s+of\s+", re.I), 2),
    (re.compile(r"^\s*students?\s+(should|will|must)\s+(know|learn|understand)\s+", re.I), 2),
    (re.compile(r"^\s*students?\s+(should|will|must)\s+", re.I), 0),
    (re.compile(r"^\s*to\s+provide\s+(an?\s+)?(overview|understanding|knowledge)\s+of\s+", re.I), 2),
    (re.compile(r"^\s*to\s+provide\s+", re.I), 2),
    (re.compile(r"^\s*to\s+(introduce|impart|familiarize|familiarise)\s+(the\s+)?"
                r"(students?\s+(with|to)\s+)?", re.I), 2),
    (re.compile(r"^\s*to\s+(give|teach|cover|discuss)\s+", re.I), 2),
    (re.compile(r"^\s*(an?\s+)?(overview|introduction|study)\s+of\s+", re.I), 2),
    (re.compile(r"^\s*to\s+", re.I), 0),
)

# Vague verb -> the Bloom level and verb it should become.
_VAGUE_TO_VERB: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^\s*(gain\s+knowledge\s+of|become\s+aware\s+of|be\s+familiar\s+with|"
                r"be\s+aware\s+of|have\s+knowledge\s+of)\s+", re.I), 2),
    (re.compile(r"^\s*(understand(ing)?|know(ing)?|learn(ing)?|appreciate)\s+", re.I), 2),
    (re.compile(r"^\s*(study|studying)\s+", re.I), 2),
)

_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)
_BUNDLED = re.compile(
    r"\b(" + "|".join(sorted(GOOD_VERBS, key=len, reverse=True)) + r")\b", re.I
)

# A nominalised opening -- "Designing of logic gates", "Implementation of ...",
# "Analysis of ..." -- names the activity as a noun rather than something the
# student does. Real and common: three of the five outcomes in a current AKTU
# VLSI lab syllabus open this way.
#
# These must be *converted*, not prefixed. Prefixing produced "Apply Designing
# of logic gates.", which is ungrammatical and worse than leaving the original
# alone, because a college would paste it into a SAR unread.
_NOMINALISATION: dict[str, tuple[str, int]] = {
    "designing": ("Design", 6), "design": ("Design", 6),
    "implementation": ("Implement", 3), "implementing": ("Implement", 3),
    "analysis": ("Analyze", 4), "analyzing": ("Analyze", 4), "analysing": ("Analyze", 4),
    "evaluation": ("Evaluate", 5), "evaluating": ("Evaluate", 5),
    "application": ("Apply", 3), "applying": ("Apply", 3),
    "development": ("Develop", 6), "developing": ("Develop", 6),
    "construction": ("Construct", 6), "constructing": ("Construct", 6),
    "measurement": ("Measure", 3), "measuring": ("Measure", 3),
    "identification": ("Identify", 1), "identifying": ("Identify", 1),
    "description": ("Describe", 2), "describing": ("Describe", 2),
    "explanation": ("Explain", 2), "explaining": ("Explain", 2),
    "comparison": ("Compare", 4), "comparing": ("Compare", 4),
    "calculation": ("Calculate", 3), "calculating": ("Calculate", 3),
    "simulation": ("Simulate", 3), "simulating": ("Simulate", 3),
    "testing": ("Test", 3), "modelling": ("Model", 3), "modeling": ("Model", 3),
    "selection": ("Select", 3), "classification": ("Classify", 2),
    "verification": ("Validate", 5), "investigation": ("Investigate", 4),
}
# "Designing of X", "Design and analysis of X", "Implementation of X".
_NOMINAL_OPENER = re.compile(
    r"^\s*(" + "|".join(sorted(_NOMINALISATION, key=len, reverse=True)) + r")"
    r"(?:\s+and\s+(" + "|".join(sorted(_NOMINALISATION, key=len, reverse=True)) + r"))?"
    r"\s+(?:of|for)\s+", re.I)

# Verbs that are real English but absent from the Bloom tables, so the quality
# checker rejects the outcome. Each maps to the nearest measurable verb at the
# level the original implies. Without this the rewriter fell through to the
# blind prefix and mangled the sentence.
_WEAK_VERB: dict[str, tuple[str, int]] = {
    "express": ("Describe", 2), "acquire": ("Describe", 2), "recognize": ("Identify", 1),
    "recognise": ("Identify", 1), "do": ("Perform", 3), "study": ("Describe", 2),
    "gain": ("Describe", 2), "obtain": ("Describe", 2), "familiarize": ("Describe", 2),
    "comprehend": ("Explain", 2), "grasp": ("Explain", 2), "realize": ("Identify", 1),
    "outline": ("Describe", 2), "discuss": ("Explain", 2), "review": ("Examine", 4),
    "illustrate": ("Describe", 2), "recall": ("Define", 1),
}
_WEAK_VERB_OPENER = re.compile(
    r"^\s*(" + "|".join(sorted(_WEAK_VERB, key=len, reverse=True)) + r")\b"
    r"(\s+knowledge\s+(?:of|about))?\s*", re.I)

# A vague verb in the middle of a sentence -- "...basics to understand the
# concepts of equalization..." -- is still unmeasurable, but the outcome's own
# leading verb may be perfectly good. Replace the vague word in place rather
# than rebuilding the sentence around it.
_MIDSENTENCE_VAGUE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bunderstand(ing)?\b", re.I), "explain"),
    (re.compile(r"\bknow(ing)?\b", re.I), "state"),
    (re.compile(r"\blearn(ing)?\b", re.I), "describe"),
    (re.compile(r"\bappreciate\b", re.I), "explain"),
    (re.compile(r"\bbe\s+familiar\s+with\b", re.I), "describe"),
)


@dataclass
class Rewrite:
    co_code: str
    original: str
    proposed: str
    blooms_levels: list[int]
    rules_applied: list[str] = field(default_factory=list)
    changed: bool = False
    already_compliant: bool = False
    split_suggestion: list[str] = field(default_factory=list)
    note: str = ""
    # Set when the statement is not compliant and no safe rewrite exists.
    # Emitting mangled text would be worse than saying so: a college pastes
    # what the tool proposes.
    needs_review: bool = False


# Words that open a sentence as a verb even though they are not measurable
# Bloom verbs. Used to decide whether prefixing a verb would produce
# "Apply Designing of logic gates." rather than a sentence.
_VERBISH = re.compile(
    r"^(?:[a-z]+(?:ise|ize|ate|ify|ect|ent|end|ess|ord|orm|uce|ute|ive|ike)"
    r"|do|be|use|make|get|give|show|find|deal|carry|put|take|know|handle"
    r"|[a-z]+ing)\b", re.I)

# "Bootstrap the subsystem", "Do the layout", "Express the knowledge" -- a
# word followed by a determiner is being used as a verb. A noun-phrase
# subject takes a preposition instead: "Faults in digital circuits".
_VERB_THEN_DETERMINER = re.compile(
    r"^[A-Za-z]+\s+(?:the|an?|its|their|your|our|this|these|those|various|"
    r"different|all|both|each|every)\b", re.I)


def _starts_like_a_verb(text: str) -> bool:
    """
    True when the first word reads as a verb or gerund, so prefixing another
    verb in front of it would produce nonsense.
    """
    text = text.strip()
    return bool(_VERBISH.match(text) or _VERB_THEN_DETERMINER.match(text))


def _titlecase_first(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def _ensure_period(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith(".") else text + "."


def _strip_opener(text: str) -> tuple[str, int | None, str | None]:
    """Remove a teacher-intent opener; return (remainder, implied level, rule)."""
    for pattern, level in _OPENER_RULES:
        m = pattern.match(text)
        if m:
            return text[m.end():].strip(), (level or None), f"Removed opener {m.group(0).strip()!r}"
    return text, None, None


def _strip_vague_verb(text: str) -> tuple[str, int | None, str | None]:
    for pattern, level in _VAGUE_TO_VERB:
        m = pattern.match(text)
        if m:
            return text[m.end():].strip(), level, f"Replaced vague verb {m.group(0).strip()!r}"
    return text, None, None


def _convert_nominalisation(text: str) -> tuple[str, int | None, str | None]:
    """"Designing of logic gates" -> ("Design logic gates", 6, rule)."""
    m = _NOMINAL_OPENER.match(text)
    if m:
        first, _ = _NOMINALISATION[m.group(1).lower()]
        rest = text[m.end():].strip()
        if m.group(2):
            second, lvl2 = _NOMINALISATION[m.group(2).lower()]
            level = max(_NOMINALISATION[m.group(1).lower()][1], lvl2)
            return (f"{first} and {second.lower()} {rest}", level,
                    f"Converted the noun phrase {m.group(0).strip()!r} into "
                    f"{first.lower()} and {second.lower()}")
        level = _NOMINALISATION[m.group(1).lower()][1]
        return (f"{first} {rest}", level,
                f"Converted the noun phrase {m.group(0).strip()!r} into "
                f"the action verb {first!r}")

    # "Designing logic gates" -- nominalisation with no "of".
    lead = re.match(r"^\s*([A-Za-z]+)\s+", text)
    if lead and lead.group(1).lower() in _NOMINALISATION:
        word = lead.group(1).lower()
        if word not in GOOD_VERBS:
            verb, level = _NOMINALISATION[word]
            return (f"{verb} {text[lead.end():].strip()}", level,
                    f"Converted {lead.group(1)!r} into the action verb {verb!r}")
    return text, None, None


def _replace_weak_verb(text: str) -> tuple[str, int | None, str | None]:
    """"Express the basic knowledge of X" -> ("Describe X", 2, rule)."""
    m = _WEAK_VERB_OPENER.match(text)
    if not m:
        return text, None, None
    word = m.group(1).lower()
    if word in GOOD_VERBS:
        return text, None, None
    verb, level = _WEAK_VERB[word]
    rest = text[m.end():].strip()
    rest = re.sub(r"^(the\s+)?(basic\s+|fundamental\s+)?knowledge\s+(of|about)\s+",
                  "", rest, flags=re.I).strip()
    rest = _ARTICLE.sub("", rest).strip()
    return (f"{verb} {rest}", level,
            f"Replaced {m.group(0).strip()!r} with the measurable verb {verb!r}")


def _replace_midsentence_vague(text: str) -> tuple[str, list[str]]:
    rules: list[str] = []
    for pattern, replacement in _MIDSENTENCE_VAGUE:
        # Only past the first word: a leading vague verb is handled earlier.
        head, _, tail = text.partition(" ")
        if not tail:
            continue
        new_tail, n = pattern.subn(replacement, tail)
        if n:
            text = f"{head} {new_tail}"
            rules.append(
                f"Replaced the unmeasurable {pattern.pattern.strip(chr(92) + 'b')!r} "
                f"with {replacement!r}"
            )
    return text, rules


def _detect_bundled_verbs(text: str) -> list[str]:
    seen, out = set(), []
    for m in _BUNDLED.finditer(text):
        v = m.group(1).lower()
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def rewrite_co(
    co_code: str,
    statement: str,
    *,
    declared_blooms_levels: list[int] | None = None,
) -> Rewrite:
    """
    Rewrite one CO into compliant form. If it is already compliant, it is
    returned untouched with `already_compliant=True` -- the rewriter must not
    churn text that is fine, or faculty stop trusting the diff.
    """
    original = statement.strip()
    declared = sorted(set(declared_blooms_levels or []))

    if not original:
        lvl = declared[-1] if declared else 3
        return Rewrite(
            co_code, original,
            f"{_CANONICAL_VERB[lvl]} <subject matter> <under what conditions>.",
            [lvl], ["Empty statement — a template was inserted."], changed=True,
            note="Nothing to rewrite; fill in the subject matter.",
        )

    baseline = check_co(co_code, original, declared_blooms_levels=declared or None)
    rules: list[str] = []
    text = original
    implied: int | None = None

    text, lvl, rule = _strip_opener(text)
    if rule:
        rules.append(rule)
        implied = implied or lvl
    text, lvl, rule = _strip_vague_verb(text)
    if rule:
        rules.append(rule)
        implied = lvl or implied

    # Re-check the opener: "To provide an understanding of X" needs two passes.
    text2, lvl2, rule2 = _strip_vague_verb(text)
    if rule2:
        rules.append(rule2)
        text, implied = text2, (lvl2 or implied)

    text = _ARTICLE.sub("", text).strip()
    text = re.sub(r"^(of|about|on|with|in)\s+", "", text, flags=re.I).strip()

    # "Designing of logic gates" -> "Design logic gates".
    verb_inserted = False
    text, lvl, rule = _convert_nominalisation(text)
    if rule:
        rules.append(rule)
        implied = lvl or implied
        verb_inserted = True
    # "Express the basic knowledge of X" -> "Describe X".
    text, lvl, rule = _replace_weak_verb(text)
    if rule:
        rules.append(rule)
        implied = lvl or implied
        verb_inserted = True

    if baseline.is_usable and not rules:
        # Compliant phrasing can still bundle several outcomes into one, so
        # the split check runs here too rather than only on rewritten text.
        bundled = _detect_bundled_verbs(original)
        split = (
            [f"{_titlecase_first(v)} … (separate outcome)" for v in bundled[:3]]
            if len(bundled) > 2 else []
        )
        return Rewrite(
            co_code, original, original, declared or ([baseline.detected_bloom_level or 3]),
            ([f"Contains {len(bundled)} action verbs ({', '.join(bundled)}) — likely "
              f"several outcomes bundled into one. Splitting is suggested, not applied."]
             if split else []),
            changed=False, already_compliant=True, split_suggestion=split,
            note=("Measurable and action-verb led, but consider splitting it."
                  if split else
                  "Already measurable and led by an action verb — left unchanged."),
        )

    # Choose the Bloom level: the faculty's declaration wins, then whatever
    # the original phrasing implied, then a safe default.
    level = (declared[-1] if declared else None) or implied or 3
    level = max(1, min(6, level))

    # A vague verb can sit mid-sentence behind a perfectly good leading one:
    # "Apply the knowledge of ... to understand the concepts of ...".
    text, vague_rules = _replace_midsentence_vague(text)
    rules.extend(vague_rules)

    lowered = text.lower()
    leading = re.match(r"^([a-z]+)", lowered)
    # A conversion above already put a chosen verb at the front. Prefixing
    # again produced "Apply Measure microwave power." -- the verb list simply
    # did not happen to contain the word we had just inserted.
    has_verb = verb_inserted or bool(leading and leading.group(1) in GOOD_VERBS)

    if has_verb:
        proposed = _titlecase_first(text)
        rules.append("Kept the existing action verb.")
    elif not text:
        verb = _CANONICAL_VERB[level]
        proposed = f"{verb} <subject matter>"
        rules.append(f"Added the action verb {verb!r} for Bloom level K{level}.")
    elif _starts_like_a_verb(text):
        # An unrecognised but plausible verb: prefixing would double it up.
        # Keep the sentence and let a human judge, rather than emitting
        # something ungrammatical that would be pasted into a SAR unread.
        return Rewrite(
            co_code, original, original, declared or [level], rules,
            changed=False, already_compliant=False,
            needs_review=True,
            note=(
                f"Begins with {leading.group(1)!r}, which is not a measurable "
                f"Bloom verb, and no safe substitution is defined for it. "
                f"Rewrite it to start with a verb such as "
                f"{_CANONICAL_VERB[level]} — left unchanged rather than "
                f"guessed at."
            ),
        )
    else:
        verb = _CANONICAL_VERB[level]
        proposed = f"{verb} {text}"
        rules.append(
            f"Added the action verb {verb!r} for Bloom level K{level} "
            f"({BLOOM_LEVEL_VERBS[level][0]})."
        )

    proposed = _ensure_period(re.sub(r"\s+", " ", proposed).strip())

    # Flag bundling rather than silently splitting: which outcomes to separate
    # is a course-design decision, not a text transformation.
    split: list[str] = []
    verbs = _detect_bundled_verbs(proposed)
    if len(verbs) > 2:
        split = [
            f"{_titlecase_first(v)} … (separate outcome)" for v in verbs[:3]
        ]
        rules.append(
            f"Contains {len(verbs)} action verbs ({', '.join(verbs)}) — likely several "
            f"outcomes bundled into one. Splitting is suggested, not applied."
        )

    levels = declared or [level]
    if len(levels) > 1:
        rules.append(
            f"Tagged K{','.join(f'{l}' for l in levels)}; attainment will use the "
            f"highest (K{max(levels)}). A single dominant level maps more cleanly."
        )

    return Rewrite(
        co_code, original, proposed, levels, rules,
        changed=(proposed.strip().lower() != original.strip().lower()),
        already_compliant=False, split_suggestion=split,
        note=("Retain both: the university-published statement for provenance, "
              "the department-adopted one for attainment."),
    )


def rewrite_all(
    cos: dict[str, tuple[str, list[int]]],
) -> dict[str, Rewrite]:
    return {
        code: rewrite_co(code, statement, declared_blooms_levels=blooms)
        for code, (statement, blooms) in cos.items()
    }
