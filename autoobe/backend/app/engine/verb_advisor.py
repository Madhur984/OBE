"""
Giving a course outcome a verb that can actually be assessed.

The client's guidelines list thirteen good verbs and six to avoid:

    good   define, identify, explain, apply, analyze, design, evaluate,
           develop, implement, demonstrate, solve, compare, differentiate
    avoid  know, understand, learn, gain knowledge, be familiar with,
           appreciate

Two things go wrong in published syllabi, and this module handles both.

**A vague verb.** "Understand the fundamentals of IoT" cannot be assessed:
nothing a student does would settle whether they understood.

**No verb at all.** "Application of IoT in Industrial and Commercial Building
Automation.", "Designing of logic gates.", "Interface mechanism of different
Sensors...". These name the activity as a *noun*, so there is not even a
wrong action to assess — which is worse than a vague one, not better. Three
of the five outcomes in one AKTU IoT elective are written this way, and 5 of
the 189 outcomes across the three published schemes in tests/fixtures.

The fix is never a blind substitution, because **the verb decides the mapping
level**. It sets the CO's Bloom level, Bloom sets CL, and under Rule 2 a
strong match with CL < PL maps at CL. Swapping "Understand" (K2, CL 1) for
"Design" (K6, CL 3) silently turns level-1 cells into level-3 cells across
the whole row.

So this module does three things and refuses to do a fourth:

  * it ranks the good verbs against what the sentence is actually about;
  * it reports the Bloom level and action level each choice would produce,
    and whether that agrees with the level the syllabus declared;
  * it rewrites the sentence cleanly -- taking the preposition the vague
    phrase was carrying ("gain knowledge OF x" -> "explain x", not "explain
    of x"), converting the noun ("Application of x" -> "Apply x"), and
    supplying an article where the outcome is a bare noun phrase ("Interface
    mechanism of x" -> "Explain THE interface mechanism of x").

It does not choose. A verb change moves numbers that reach a SAR, so the
final say stays with the faculty member, and the UI shows the consequence
next to each option.

None of this needs an API key. `offline_recommendation()` is the actual
recommendation and runs entirely on the tables below; `ai_recommendation()`
is a second opinion that is absent, not fabricated, when no key is set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.engine.blooms import VAGUE_VERBS, action_level_for_bloom
from app.engine.co_quality import GOOD_VERBS

# The thirteen from the guidelines, in the order they are listed there.
GUIDELINE_VERBS: tuple[str, ...] = (
    "define", "identify", "explain", "apply", "analyze", "design", "evaluate",
    "develop", "implement", "demonstrate", "solve", "compare", "differentiate",
)

# A vague opening, with the preposition it drags along. "be familiar with the
# basics" must become "explain the basics" -- keeping the "with" is the
# obvious bug and it reads as authored text, so it would survive review.
_VAGUE_OPENER = re.compile(
    r"^\s*(?:to\s+)?("
    + "|".join(sorted((re.escape(v) for v in VAGUE_VERBS), key=len, reverse=True))
    + r")\b(?:\s+(?:about|of|with|that))?\s*", re.I)
# "how" is deliberately NOT stripped: "Understand how to represent X" must
# become "Explain how to represent X", not "Explain to represent X".

# ------------------------------------------- outcomes with no verb at all

# The noun the author reached for is evidence, exactly the way a buried verb
# is: whoever wrote "Application of IoT..." meant "apply". Only the thirteen
# guideline verbs appear on the right, because those are the only ones this
# module is allowed to offer.
NOMINAL_VERB: dict[str, str] = {
    "application": "apply", "applications": "apply", "applying": "apply",
    "implementation": "implement", "implementing": "implement",
    "programming": "implement", "coding": "implement",
    "simulation": "implement", "interfacing": "implement",
    "design": "design", "designing": "design",
    "development": "develop", "developing": "develop",
    "building": "develop", "construction": "develop", "constructing": "develop",
    "creation": "develop", "modelling": "develop", "modeling": "develop",
    "analysis": "analyze", "analyses": "analyze", "analyzing": "analyze",
    "analysing": "analyze", "investigation": "analyze", "examination": "analyze",
    "evaluation": "evaluate", "evaluating": "evaluate",
    "assessment": "evaluate", "verification": "evaluate", "validation": "evaluate",
    "comparison": "compare", "comparing": "compare",
    "differentiation": "differentiate", "distinction": "differentiate",
    "identification": "identify", "identifying": "identify",
    "classification": "identify", "selection": "identify",
    "definition": "define", "definitions": "define",
    "description": "explain", "describing": "explain",
    "explanation": "explain", "explaining": "explain",
    "introduction": "explain", "overview": "explain", "study": "explain",
    "understanding": "explain", "knowledge": "explain", "awareness": "explain",
    "familiarity": "explain", "review": "explain", "working": "explain",
    "demonstration": "demonstrate", "measurement": "demonstrate",
    "measuring": "demonstrate", "testing": "demonstrate",
    "solution": "solve", "calculation": "solve", "computation": "solve",
}

# Words that read as a verb even though `co_quality.GOOD_VERBS` does not list
# them. Used ONLY to decide that an outcome already has a verb and must be
# left alone -- never to assign a Bloom level, so nothing here can move a
# mapping cell. Without it "Analyse and design basic digital IC circuits"
# (BEC501 CO4, British spelling) and "Appraise the steady state error"
# (BEC652 CO5) would be flagged as verbless, which is plainly wrong and would
# cost the tool its credibility on the first screenful.
_ALSO_VERBS = frozenset({
    "analyse", "appraise", "distinguish", "state", "illustrate", "derive",
    "sketch", "draw", "plot", "write", "program", "choose", "name", "outline",
    "discuss", "predict", "relate", "integrate", "compile", "label", "match",
    "translate", "infer", "judge", "rank", "defend", "propose", "devise",
    "produce", "express", "acquire", "recognize", "recognise", "realize",
    "realise", "comprehend", "grasp", "obtain", "familiarize", "familiarise",
    "do", "handle", "operate", "study", "discover", "explore", "deduce",
    "simulate", "verify", "check", "ensure",
})

# Abstract nouns that turn the word before them into a compound noun rather
# than a verb: "Interface MECHANISM of ...", "Design METHODOLOGY for ...".
# Requiring one of these is what keeps the noun-phrase rule from firing on
# "Interface sensors with a microcontroller", which is an instruction.
_ABSTRACT_NOUN = (
    "mechanism", "mechanisms", "method", "methods", "methodology", "technique",
    "techniques", "process", "processes", "procedure", "procedures", "concept",
    "concepts", "principle", "principles", "structure", "architecture", "model",
    "models", "scheme", "schemes", "approach", "technology", "technologies",
    "requirement", "requirements", "characteristic", "characteristics",
    "behaviour", "behavior", "performance", "aspect", "aspects", "issue",
    "issues", "feature", "features", "function", "functions", "role", "roles",
    "importance", "significance", "scope", "basics", "fundamentals", "overview",
    "types", "classification", "working", "operation", "operations", "criteria",
    "factors", "parameters", "effect", "effects", "impact", "study", "studies",
    "knowledge", "theory", "theories", "steps", "stages", "levels", "layers",
)


def _known_verbs() -> frozenset[str]:
    from app.engine.blooms import BLOOM_LEVEL_VERBS  # noqa: PLC0415
    words = set(GOOD_VERBS) | set(_ALSO_VERBS)
    for _name, verbs in BLOOM_LEVEL_VERBS.values():
        words.update(verbs)
    return frozenset(words)


_KNOWN_VERBS = _known_verbs()

_LEADING_TO = re.compile(r"^\s*to\s+", re.I)
# An outcome may not open with a gerund: "Building X" is a noun phrase, not
# something a student is asked to do. This holds even when the stem is a
# perfectly good verb -- "Building" comes from "build", and the outcome is
# still not assessable until it says "Develop ...".
_GERUND_HEAD = re.compile(r"^\s*([A-Za-z]+ing)\b")
# ...with one exception: a gerund that introduces a modifier rather than
# naming the activity. "Using MATLAB, design a low pass filter" has a verb;
# stripping "Using" would produce "Design MATLAB, design a low pass filter".
_PARTICIPIAL = frozenset({
    "using", "utilizing", "utilising", "employing", "including", "considering",
    "regarding", "concerning", "involving", "having", "being", "following",
    "assuming", "depending", "referring", "relating", "corresponding",
})
# "Applying", "Working" and "Designing" stay out of that set on purpose:
# "Applying the concepts of X" and "Working of a transistor" are headings, and
# the clause rule below catches the participial reading of the same words.
# The same shape without a stop word: a leading clause closed by a comma, with
# a real verb after it -- "Working in a team, develop a prototype."
_PARTICIPIAL_CLAUSE = re.compile(r"^\s*[A-Za-z]+ing\b[^,.]{0,60},\s*([A-Za-z]+)")
# "Application of X", "Introduction to X", "Design and analysis of X".
_NOMINAL_HEADS = "|".join(sorted(NOMINAL_VERB, key=len, reverse=True))
_NOMINAL_OPENER = re.compile(
    r"^\s*(" + _NOMINAL_HEADS + r")"
    r"(?:\s+and\s+(?:" + _NOMINAL_HEADS + r"))?"
    r"\s+(?:of|to|for|in|on)\b\s*", re.I)
# When the head doubles as a real verb -- "design", "study", "review" -- only
# a following "of"/"to" settles it. "Design OF logic gates" is a noun phrase;
# "Design FOR testability" is an instruction, and flagging it would be wrong.
_NOMINAL_OPENER_STRICT = re.compile(
    r"^\s*(" + _NOMINAL_HEADS + r")"
    r"(?:\s+and\s+(?:" + _NOMINAL_HEADS + r"))?"
    r"\s+(?:of|to)\b\s*", re.I)
_DETERMINER = (r"(?:the|an?|its|their|your|our|this|that|these|those|various|"
               r"different|all|both|each|every|any|some|several|basic|major|"
               r"how|what|why|when|where)")
# "Interface mechanism of ..." -- an unrecognised head word, an abstract noun,
# then a preposition. All three conditions are needed: drop any one of them
# and the rule starts flagging instructions such as "Interface sensors with a
# microcontroller board".
_NOUN_PHRASE_HEAD = re.compile(
    r"^\s*([A-Za-z]+)\s+(?!" + _DETERMINER + r"\b)"
    r"(" + "|".join(sorted(_ABSTRACT_NOUN, key=len, reverse=True)) + r")\s+"
    r"(?:of|for|in|with|to)\b", re.I)

# Capitals that are an artefact of sitting at the front of a sentence, not a
# proper noun. Anything outside this list keeps the author's capital, because
# lowercasing "IoT", "MOS" or "VHDL" would be worse than an odd-looking
# capital mid-sentence.
_SAFE_DECAP = frozenset("""
state states various different basic basics fundamental fundamentals advanced
modern real concept concepts knowledge design designs analysis architecture
architectures mechanism mechanisms interface interfaces application
applications performance working operation operations principle principles
technique techniques method methods structure structure structures
implementation development study overview introduction use uses role
characteristics behaviour behavior effect effects types type
""".split())

# What the sentence is about, and which verbs that favours. Every term is a
# word-start match, the same rule the competency lexicon uses.
_CONTENT_AFFINITY: dict[str, tuple[str, ...]] = {
    "difference": ("differentiate", "compare"),
    "compar": ("compare", "differentiate"),
    "between": ("differentiate", "compare"),
    "versus": ("compare", "differentiate"),
    "classif": ("identify", "differentiate"),
    "type": ("identify", "differentiate"),
    "definition": ("define", "identify"),
    "terminolog": ("define", "explain"),
    "concept": ("explain", "define"),
    "principle": ("explain", "apply"),
    "fundamental": ("explain", "define"),
    "basic": ("explain", "define"),
    "theory": ("explain", "apply"),
    "structure": ("explain", "identify"),
    "mechanism": ("explain", "demonstrate"),
    "architecture": ("explain", "design"),
    "working": ("explain", "demonstrate"),
    "operation": ("explain", "demonstrate"),
    "performance": ("analyze", "evaluate"),
    "behaviour": ("analyze", "evaluate"),
    "behavior": ("analyze", "evaluate"),
    "characteristic": ("analyze", "explain"),
    "effect": ("analyze", "evaluate"),
    "impact": ("analyze", "evaluate"),
    "response": ("analyze", "evaluate"),
    "stability": ("analyze", "evaluate"),
    "problem": ("solve", "apply"),
    "numerical": ("solve", "apply"),
    "calculat": ("solve", "apply"),
    "circuit": ("design", "analyze"),
    "system": ("design", "analyze"),
    "model": ("develop", "apply"),
    "algorithm": ("implement", "apply"),
    "program": ("implement", "develop"),
    "code": ("implement", "develop"),
    "software": ("implement", "apply"),
    "tool": ("demonstrate", "apply"),
    "instrument": ("demonstrate", "apply"),
    "experiment": ("demonstrate", "analyze"),
    "measurement": ("demonstrate", "analyze"),
    "protocol": ("explain", "implement"),
    "technique": ("apply", "analyze"),
    "method": ("apply", "analyze"),
    "application": ("apply", "identify"),
    "suitab": ("evaluate", "compare"),
    "advantage": ("evaluate", "compare"),
    "limitation": ("evaluate", "analyze"),
    "trade-off": ("evaluate", "compare"),
    "criteria": ("evaluate", "compare"),
    "requirement": ("design", "identify"),
    "specification": ("design", "identify"),
    "prototype": ("develop", "design"),
}

CONTENT_WEIGHT = 1.0
SECOND_CHOICE_WEIGHT = 0.5
# "Design the design requirements", "Differentiate the difference between"
# -- a verb that repeats the word right after it reads like a machine wrote
# it, and a faculty member would have to fix it by hand anyway.
REDUNDANT_PENALTY = 2.6
# A verb the outcome already names behind the vague opener beats any
# guess: it is what the author meant, in their own words.
BURIED_VERB_WEIGHT = 4.0
# The same evidence in noun form: "Application of..." was written by someone
# who meant "apply". Weighted with the buried verb, and deliberately above
# DECLARED_LEVEL_WEIGHT -- when a syllabus tags "Designing of logic gates" as
# K2 the tag is what is wrong, not the word the author chose.
NOMINAL_VERB_WEIGHT = 4.0
# The syllabus already said how demanding this outcome is. A replacement that
# keeps that level changes no mapping cell; one that does not moves the whole
# row, so the agreeing options are offered first.
DECLARED_LEVEL_WEIGHT = 2.5
NEAR_LEVEL_WEIGHT = 0.8


@dataclass(frozen=True)
class VerbSuggestion:
    verb: str
    bloom_level: int
    action_level: int
    score: float
    why: str
    keeps_declared_level: bool

    def to_dict(self) -> dict:
        return {
            "verb": self.verb, "bloom_level": self.bloom_level,
            "action_level": self.action_level, "score": round(self.score, 2),
            "why": self.why, "keeps_declared_level": self.keeps_declared_level,
        }


def detect_vague_opening(statement: str) -> str | None:
    """The vague phrase this outcome opens with, if any."""
    m = _VAGUE_OPENER.match(statement or "")
    return m.group(1).lower() if m else None


def strip_vague_opening(statement: str) -> str:
    """The outcome with its vague opening and trailing preposition removed."""
    m = _VAGUE_OPENER.match(statement or "")
    return (statement[m.end():] if m else statement).strip()


def _is_participial(body: str, gerund: str) -> bool:
    """
    True when the leading `-ing` word modifies a real instruction instead of
    replacing one -- "Using MATLAB, design a low pass filter."
    """
    if gerund.lower() in _PARTICIPIAL:
        return True
    clause = _PARTICIPIAL_CLAUSE.match(body)
    return bool(clause and clause.group(1).lower() in _KNOWN_VERBS)


def detect_verbless_opening(statement: str) -> str | None:
    """
    The noun phrase this outcome opens with instead of a verb, if any.

    Returns None whenever the outcome already leads with something a student
    could be asked to do -- including verbs the Bloom tables do not happen to
    list. A false positive here tells a faculty member their perfectly good
    outcome is broken, so the rules below are deliberately narrow: a
    recognised nominalisation, a gerund, or a bare noun-noun-preposition
    phrase. Across the 189 outcomes in the three published schemes this
    flags 5 and nothing else.
    """
    text = (statement or "").strip()
    if not text or detect_vague_opening(text):
        return None                       # the other detector owns that case
    body = _LEADING_TO.sub("", text)      # "To design X" has a verb
    head_m = re.match(r"^([A-Za-z]+)", body)
    if not head_m:
        return None
    head = head_m.group(1).lower()
    gerund = _GERUND_HEAD.match(body)
    if gerund and _is_participial(body, gerund.group(1)):
        return None                       # a modifier in front of a real verb

    if head in _KNOWN_VERBS and not gerund:
        # A word that is genuinely a verb, unless a preposition follows it
        # straight away: "Design of logic gates" is not an instruction.
        m = _NOMINAL_OPENER_STRICT.match(body)
        return m.group(0).strip() if m and head in NOMINAL_VERB else None

    m = _NOMINAL_OPENER.match(body)
    if m:
        return m.group(0).strip()
    if gerund:
        return gerund.group(1)
    m = _NOUN_PHRASE_HEAD.match(body)
    if m and m.group(1).lower() not in _KNOWN_VERBS:
        return f"{m.group(1)} {m.group(2)}"
    return None


def detect_issue(statement: str) -> str | None:
    """`"vague"`, `"verbless"`, or None when the outcome opens acceptably."""
    if detect_vague_opening(statement):
        return "vague"
    if detect_verbless_opening(statement):
        return "verbless"
    return None


def nominal_verb(statement: str) -> str | None:
    """
    The guideline verb the author's own noun implies.

    "Application of IoT..." was written by someone who meant "apply". That is
    the same kind of evidence as a verb buried behind a vague opener, and it
    beats any guess made from the subject matter.
    """
    phrase = detect_verbless_opening(statement)
    if not phrase:
        return None
    head = re.match(r"^([A-Za-z]+)", phrase)
    return NOMINAL_VERB.get(head.group(1).lower()) if head else None


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _decap(text: str) -> str:
    """Undo a sentence-initial capital, but only on a word that is never a name."""
    first = re.match(r"^([A-Za-z]+)", text)
    if first and first.group(1).lower() in _SAFE_DECAP:
        return first.group(1).lower() + text[first.end():]
    return text


_TO_VERB = re.compile(r"^to\s+([a-z]+)\b", re.I)
_OPENS_WITH_DETERMINER = re.compile(r"^\s*" + _DETERMINER + r"\b", re.I)


def _open_body(statement: str) -> tuple[str, bool]:
    """
    The part of the outcome a replacement verb should govern, plus whether it
    needs an article in front of it.

    A bare noun phrase does: "Interface mechanism of X" becomes "Explain THE
    interface mechanism of X". A converted nominalisation does not: the noun
    took the article with it.
    """
    vague = _VAGUE_OPENER.match(statement or "")
    if vague:
        return statement[vague.end():].strip(), False
    text = (statement or "").strip()
    if not detect_verbless_opening(text):
        # Nothing is wrong with this one, so nothing here will offer to change
        # it. A caller who asks anyway means a deliberate swap: replace the
        # verb rather than stack a second one in front of it, which is how
        # "Explain Analyze the stability of..." gets written.
        head = re.match(r"^([A-Za-z]+)\s", _LEADING_TO.sub("", text))
        if head and head.group(1).lower() in _KNOWN_VERBS:
            return _LEADING_TO.sub("", text)[head.end():].strip(), False
        return text, False
    body = _LEADING_TO.sub("", text)
    head = re.match(r"^([A-Za-z]+)", body)
    head_word = head.group(1).lower() if head else ""
    opener = (_NOMINAL_OPENER_STRICT if head_word in _KNOWN_VERBS
              else _NOMINAL_OPENER).match(body)
    if opener:
        return _decap(body[opener.end():].strip()), False
    gerund = _GERUND_HEAD.match(body)
    if gerund:
        # The preposition belongs to the noun and goes with it, the same way
        # it does for a vague opener: "Working OF a transistor" must become
        # "Explain a transistor", never "Explain of a transistor".
        rest = re.sub(r"^(?:of|for|in|on|to|with|about)\s+", "",
                      body[gerund.end():].strip(), flags=re.I)
        return _decap(rest), False
    return _decap(body), True


def buried_verb(statement: str) -> str | None:
    """
    The real verb, when the vague opening was only wrapping one.

    "Learn TO SOLVE numerical problems" is not missing an action verb — it is
    hiding one behind "learn". Prefixing another verb gives "Apply to solve
    numerical problems", which is worse than what we started with.
    """
    m = _TO_VERB.match(strip_vague_opening(statement))
    verb = m.group(1).lower() if m else None
    return verb if verb in GOOD_VERBS else None


def apply_verb(statement: str, verb: str) -> str:
    """
    Rewrite the outcome to open with `verb`.

    Only the opening is touched. Rewriting further would put words in a
    faculty member's mouth in a document they have to sign.
    """
    verb = (verb or "").strip().lower()
    if not verb:
        return statement
    rest, needs_article = _open_body(statement)
    if not rest:
        return statement
    m = _TO_VERB.match(rest)
    if m and m.group(1).lower() in GOOD_VERBS:
        rest = rest[m.start(1):]                 # "to solve x" -> "solve x"
        needs_article = False
    if needs_article and not _OPENS_WITH_DETERMINER.match(rest):
        rest = "the " + rest                     # "Explain THE interface mechanism"
    if rest.lower().startswith(verb + " "):
        out = _cap(rest)                         # already opens with it
    else:
        out = f"{_cap(verb)} {rest}"
    out = out.strip()
    return out if out.endswith(".") else out + "."


def suggest_verbs(statement: str, *, blooms_levels: list[int] | None = None,
                  limit: int = 6) -> list[VerbSuggestion]:
    """
    Rank the guideline verbs for this outcome, best first.

    Returns an empty list when the outcome opens acceptably -- there is
    nothing to fix, and offering to change a working verb invites exactly the
    silent level drift this module exists to prevent.
    """
    issue = detect_issue(statement)
    if not issue:
        return []

    body = _open_body(statement)[0].lower()
    declared = max(blooms_levels or [], default=0)
    declared_action = action_level_for_bloom([declared]) if declared else 0

    buried = buried_verb(statement)
    nominal = nominal_verb(statement)
    hits: dict[str, list[str]] = {}
    scores: dict[str, float] = {v: 0.0 for v in GUIDELINE_VERBS}
    for term, verbs in _CONTENT_AFFINITY.items():
        if not re.search(r"(?<![a-z])" + re.escape(term), body):
            continue
        for rank, verb in enumerate(verbs):
            if verb not in scores:
                continue
            scores[verb] += CONTENT_WEIGHT if rank == 0 else SECOND_CHOICE_WEIGHT
            hits.setdefault(verb, []).append(term)

    out: list[VerbSuggestion] = []
    for verb in GUIDELINE_VERBS:
        bloom = GOOD_VERBS[verb]
        action = action_level_for_bloom([bloom])
        score = scores[verb]
        keeps = bool(declared) and bloom == declared
        if declared:
            if bloom == declared:
                score += DECLARED_LEVEL_WEIGHT
            elif action == declared_action:
                score += NEAR_LEVEL_WEIGHT
        if verb == buried:
            score += BURIED_VERB_WEIGHT
        elif verb == nominal:
            score += NOMINAL_VERB_WEIGHT
        elif any(w.startswith(verb[:5]) for w in body.split()[:4]):
            score -= REDUNDANT_PENALTY
        terms = hits.get(verb, [])
        if verb == nominal:
            why = (f"the outcome already names it: whoever wrote "
                   f"“{detect_verbless_opening(statement)}” meant “{verb}”")
        elif terms and keeps:
            why = (f"matches the declared K{declared} and the outcome is about "
                   + ", ".join(sorted(set(terms))[:3]))
        elif terms:
            why = "the outcome is about " + ", ".join(sorted(set(terms))[:3])
        elif verb == buried:
            why = "the outcome already says so: it was hidden behind the vague verb"
        elif keeps:
            why = f"keeps the declared K{declared}, so no mapping level moves"
        else:
            why = f"a K{bloom} option from the guidelines"
        out.append(VerbSuggestion(verb, bloom, action, score, why, keeps))

    out.sort(key=lambda s: (-s.score, GUIDELINE_VERBS.index(s.verb)))
    return out[:limit]


def offline_recommendation(statement: str, *,
                           blooms_levels: list[int] | None = None) -> dict | None:
    """
    The recommendation, computed here, with no API key and no network.

    This is not a fallback for `ai_recommendation` -- it is the other way
    round. The tables above encode what the guidelines say, what the author
    already wrote, and what the syllabus declared; a model adds an opinion on
    top of that when one is available. Reporting the runner-up and the margin
    matters: when two verbs score alike, saying so is more useful than
    pretending the first one was chosen for a reason.
    """
    ranked = suggest_verbs(statement, blooms_levels=blooms_levels, limit=13)
    if not ranked:
        return None
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin = round(top.score - runner.score, 2) if runner else None
    return {
        "verb": top.verb,
        "bloom_level": top.bloom_level,
        "action_level": top.action_level,
        "reason": top.why,
        "preview": apply_verb(statement, top.verb),
        "runner_up": runner.verb if runner else None,
        "margin": margin,
        # A tie is not a recommendation. Below this the UI says both are fine
        # rather than implying the first was picked on evidence.
        "clear": margin is None or margin >= 0.5,
        "source": "offline",
    }


_AI_SYSTEM = (
    "You help an engineering college rewrite a course outcome so it can be "
    "assessed. Reply with JSON only: "
    '{"verb": "<one of the allowed verbs>", "reason": "<one short sentence>"}. '
    "Choose the verb that best fits what the outcome already says. Do not "
    "invent a verb outside the allowed list. Prefer the verb whose Bloom's "
    "level matches the level the syllabus declared, unless the sentence "
    "clearly describes a different action."
)


def ai_recommendation(statement: str, *, blooms_levels: list[int] | None = None,
                      model: str | None = None, timeout: float = 12.0) -> dict | None:
    """
    Ask the model which guideline verb fits, or return None.

    Strictly an opinion on top of the deterministic ranking, never a
    replacement for it: without a key this returns None and the ordered list
    is what the UI shows. A verb outside the guideline list is rejected rather
    than displayed, the same rule the question tagger uses for invented CO
    codes.
    """
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    from app.engine.qco_tagger import DEFAULT_MODEL, llm_available  # noqa: PLC0415

    if not llm_available() or not detect_issue(statement):
        return None

    import anthropic  # noqa: PLC0415

    model = model or os.environ.get("AUTOOBE_LLM_MODEL", DEFAULT_MODEL)
    declared = max(blooms_levels or [], default=0)
    problem = ("it opens with a vague verb that cannot be assessed"
               if detect_issue(statement) == "vague" else
               "it names the activity as a noun and has no action verb at all")
    prompt = (
        f"Course outcome: {statement}\n"
        f"Problem: {problem}\n"
        f"Declared Bloom's level: {'K' + str(declared) if declared else 'none'}\n"
        f"Allowed verbs (with Bloom's level): "
        + ", ".join(f"{v} (K{GOOD_VERBS[v]})" for v in GUIDELINE_VERBS)
        + "\nWhich allowed verb should this outcome start with?"
    )
    try:
        client = anthropic.Anthropic(timeout=timeout)
        resp = client.messages.create(
            model=model, max_tokens=150, temperature=0,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content
                      if getattr(b, "type", "") == "text").strip()
        match = _re.search(r"\{.*\}", raw, _re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
    except Exception:
        return None                       # degrade to the deterministic list

    verb = str(data.get("verb", "")).strip().lower()
    if verb not in GUIDELINE_VERBS:
        return None                       # outside the guidelines: reject
    return {
        "verb": verb,
        "bloom_level": GOOD_VERBS[verb],
        "action_level": action_level_for_bloom([GOOD_VERBS[verb]]),
        "reason": str(data.get("reason", "")).strip()[:200],
        "model": model,
        "preview": apply_verb(statement, verb),
    }


def level_change(statement: str, verb: str, *,
                 blooms_levels: list[int] | None = None) -> dict:
    """
    What this substitution would do to the numbers, stated before it is made.

    A verb change is not cosmetic: it moves CL, and under Rule 2 CL decides
    the mapping level of every strong cell in the row.
    """
    declared = max(blooms_levels or [], default=0)
    new_bloom = GOOD_VERBS.get((verb or "").strip().lower())
    if new_bloom is None:
        raise ValueError(f"{verb!r} is not one of the guideline verbs")
    old_action = action_level_for_bloom([declared]) if declared else None
    new_action = action_level_for_bloom([new_bloom])
    return {
        "verb": verb.strip().lower(),
        "old_bloom": declared or None,
        "new_bloom": new_bloom,
        "old_action_level": old_action,
        "new_action_level": new_action,
        "moves_action_level": old_action is not None and old_action != new_action,
        "preview": apply_verb(statement, verb),
    }
