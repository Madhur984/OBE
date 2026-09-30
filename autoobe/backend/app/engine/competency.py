"""
Layer 2 -- deterministic competency inference.

Proposes a competency judgement (0 no match / 1 partial / 2 strong) for every CO x PO
pair, so faculty review a filled-in matrix instead of authoring one cell at a
time. This is the difference between "2-4 hours per course" and "one bulk
approve" in the plan's automation contract.

Deliberately NOT an embedding model:

  * Reproducible. The same CO text yields the same proposal in 2026 and in
    2029 during a re-accreditation audit. An embedding model's output drifts
    the moment the model version does.
  * Explainable. Every proposal names the exact terms that fired, so the
    evidence bundle a peer team asks for is a byproduct, not an afterthought.
  * Free and instant, with no service dependency in the critical path.

Signals are derived from the verbatim PO statements themselves (see the seed
modules) plus the WK profile wording, not invented -- so the lexicon is
traceable to NBA's own text. Embedding similarity is a future refinement that
can raise recall; it must not replace this layer's audit trail.

Calibration note: the thresholds below are tuned to under-propose rather than
over-propose. A missed mapping is a cheap fix by a human; a spurious weight-3
that gets bulk-approved is exactly the failure mode the plan calls out as a
high-severity risk. The constraint solver (constraints.py) is the second net.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass, field

from app.engine.blooms import action_level_for_bloom
from app.engine.mapping_rules import Competency

# Term -> weight. Multi-word phrases are matched first and score higher
# because they are far less ambiguous than single tokens ("project
# management" is a real PO10 signal; "project" alone is not).
_PO_SIGNALS: dict[str, dict[str, float]] = {
    "ENGINEERING_KNOWLEDGE": {
        "mathematic": 1.0, "engineering fundamental": 1.2, "first principle": 1.2,
        "natural science": 1.0, "scientific principle": 1.0, "fundamental": 0.7,
        "principle": 0.6, "concept": 0.5, "theory": 0.6, "theorem": 0.8,
        "basic": 0.4, "knowledge": 0.4, "formula": 0.6, "law": 0.5,
        "circuit": 0.5, "signal": 0.5, "equation": 0.6, "architecture": 0.5,
        "model": 0.4, "algorithm": 0.5, "technique": 0.35, "method": 0.35,
        "characteristic": 0.4, "parameter": 0.4, "mechanism": 0.4,
    },
    "PROBLEM_ANALYSIS": {
        "problem analysis": 1.3, "analyze": 1.0, "analyse": 1.0, "analysis": 0.9,
        "identify": 0.7, "formulate": 0.9, "examine": 0.8, "interpret": 0.7,
        "compare": 0.8, "differentiate": 0.8, "classify": 0.6, "characterize": 0.7,
        "substantiated conclusion": 1.2, "research literature": 1.1, "complex problem": 1.0,
    },
    "DESIGN_DEVELOPMENT": {
        "design": 1.2, "develop": 1.0, "construct": 0.9, "build": 0.7,
        "synthesize": 0.9, "synthesise": 0.9, "create": 0.8, "formulate solution": 1.1,
        "implement": 0.7, "component": 0.6, "system": 0.5, "prototype": 1.0,
        "specification": 0.7, "requirement": 0.6, "architecture": 0.7,
    },
    "INVESTIGATION": {
        "investigat": 1.2, "experiment": 1.1, "design of experiment": 1.4,
        "research": 0.9, "data": 0.6, "measurement": 0.9, "observation": 0.8,
        "interpretation of data": 1.3, "valid conclusion": 1.2, "hypothesis": 1.1,
        "test": 0.6, "verify": 0.7, "validate": 0.8, "characterization": 0.9,
        "laboratory": 0.8,
    },
    "TOOL_USAGE": {
        "tool": 1.1, "software": 1.0, "simulation": 1.1, "simulator": 1.1,
        "modern engineering": 1.2, "it tool": 1.3, "package": 0.7, "platform": 0.7,
        "matlab": 1.2, "spice": 1.1, "cad": 1.2, "ide": 1.0, "framework": 0.6,
        "instrument": 0.9, "equipment": 0.8, "modelling tool": 1.2, "modeling tool": 1.2,
        "programming language": 0.9, "library": 0.6,
        "sql": 1.2, "query": 0.6, "verilog": 1.2, "vhdl": 1.2, "hdl": 1.1,
        "eda": 1.2, "laboratory instrument": 1.2, "measuring instrument": 1.2,
    },
    "ENGINEER_AND_WORLD": {
        "societal": 1.2, "society": 1.1, "environment": 1.1, "sustainab": 1.2,
        "public health": 1.2, "safety": 0.9, "legal": 0.9, "cultural": 0.8,
        "economy": 0.8, "economic impact": 1.0, "net zero": 1.4, "carbon": 1.1,
        "whole-life cost": 1.4, "resource efficiency": 1.2, "environmental impact": 1.3,
        "social": 0.8, "regulatory": 1.0, "contextual knowledge": 1.1,
        # PO6 reads "environmental aspects ... impact on sustainability";
        # WK5 names environmental impacts, WK7 public safety and sustainable
        # development. These are the instances of that.
        "ecosystem": 1.1, "biodiversity": 1.1, "pollution": 1.1, "ecolog": 1.0,
        "climate": 1.0, "emission": 1.0, "natural resource": 1.1,
        "sustainable development": 1.3, "public safety": 1.2, "waste": 0.9,
        # The vocabulary the non-technical courses actually use for this PO.
        # A course on Rural Development or Indian Tradition addresses "The
        # Engineer and Society" in words no engineering-drawn lexicon holds.
        # Every term below is absent from all 189 technical outcomes in the
        # department's own sheet, which is the collision test that matters.
        "rural": 1.0, "tradition": 0.9, "heritage": 0.9, "community": 0.9,
        "civic": 1.0, "human being": 0.9, "prosperity": 0.9, "happiness": 0.9,
    },
    "ETHICS": {
        # The bare stem alone is a weaker signal than the phrase: "ethical
        # principles" is unambiguously this PO, while "ethic" also fires on
        # passing mentions, so the phrase carries the extra weight.
        "ethical principle": 1.5, "professional ethic": 1.5, "code of ethic": 1.5,
        "ethic": 1.4, "professional responsibilit": 1.3, "human value": 1.3,
        "diversity": 1.1, "inclusion": 1.1, "inclusive": 1.0, "integrity": 1.2,
        "norms of": 1.1, "code of conduct": 1.4, "plagiarism": 1.2,
        "intellectual property": 1.1, "moral": 1.1,
        # "adhere to national & international laws", verbatim from PO7. The
        # bare word "law" is deliberately absent -- it belongs to the first
        # law of thermodynamics as often as to the statute book.
        "national law": 1.3, "international law": 1.3, "regulation": 1.0,
        "regulatory compliance": 1.2, "legislation": 1.2, "statutory": 1.2,
        "licens": 1.0, "compliance": 0.9,
        # A Universal Human Values and Professional Ethics course scored 0.0
        # here on every outcome. Its own words are these, and none of them
        # were in any lexicon -- "ethic" only ever fired on the single outcome
        # that happens to contain "ethical", and the parser was dropping that
        # one. This is the gap the department pointed at.
        #
        # "harmonious" and "harmony" are listed separately and the stem
        # "harmon" deliberately is NOT: it would match "harmonic", which is
        # everywhere in an electronics syllabus and means nothing like this.
        "unethical": 1.4, "value education": 1.3, "natural acceptance": 1.1,
        "self-exploration": 1.1, "self exploration": 1.1, "humanistic": 1.2,
        "holistic": 0.8, "harmonious": 0.9, "harmony": 0.9, "right conduct": 1.2,
        "malpractice": 1.2, "constitution": 0.9, "fundamental right": 1.2,
    },
    "TEAM_WORK": {
        "team": 1.3, "teamwork": 1.4, "group": 1.0, "collaborat": 1.2,
        "as a member": 1.2, "as an individual": 1.1, "leader": 1.0,
        "multi-disciplinary": 1.1, "multidisciplinary": 1.1, "peer": 0.8,
        # PO9 is "Function effectively as an individual, and as a member ...
        # in diverse teams". A human values course is entirely about relating
        # to other people, and the department maps it here at level 3 on every
        # outcome -- but it says so in words like these, never "team".
        #
        # Bare "relationship" is deliberately absent: "the relationship
        # between voltage and current" is not team work. The phrases carry it.
        "human-human": 1.3, "interpersonal": 1.3, "harmonious relationship": 1.2,
        "family": 1.0, "trust": 0.8, "co-existence": 0.9, "coexistence": 0.9,
    },
    "COMMUNICATION": {
        # "present" is deliberately absent as a content term -- it is an
        # adjective as often as a verb ("present day technology") and only
        # means Communication when it is the action, so it lives in
        # _VERB_AFFINITY instead. The -ing/-ation forms are unambiguous.
        "communicat": 1.3, "report": 1.0, "presenting": 1.0, "presentation": 1.2,
        "document": 0.9, "documentation": 1.1, "write": 0.8, "written": 0.9,
        "oral": 1.0, "viva": 1.0, "seminar": 0.9, "comprehend": 0.7,
        # From the Technical Communication syllabus, which is a whole course
        # against this PO and was reaching it on "communicat" alone.
        "technical writing": 1.3, "interpersonal communication": 1.3,
        "listening": 1.0, "reading comprehension": 1.1, "grammar": 0.9,
        "vocabulary": 0.8, "oral presentation": 1.3,
    },
    "PROJECT_MANAGEMENT": {
        "project management": 1.5, "management principle": 1.3, "cost": 0.9,
        "costing": 1.1, "budget": 1.2, "finance": 1.2, "financial": 1.1,
        "schedule": 1.0, "scheduling": 1.1, "resource allocation": 1.2,
        "economic decision": 1.3, "feasibility": 1.0, "planning": 0.8,
        # Project Management and Entrepreneurship is a whole course against
        # this PO; "entrepreneur" appeared in no lexicon at all.
        "entrepreneur": 1.3, "startup": 1.1, "start-up": 1.1, "venture": 1.0,
        "business plan": 1.2, "business model": 1.2,
    },
    "LIFELONG_LEARNING": {
        "life-long": 1.4, "lifelong": 1.4, "independent learning": 1.3,
        "self-study": 1.2, "self study": 1.2, "emerging technolog": 1.3,
        "new technolog": 1.1, "critical thinking": 1.2, "adapt": 0.8,
        "continuing": 0.9, "recent trend": 1.1, "state of the art": 1.1,
        "current research": 1.0,
        # "adaptability to new and emerging technologies" and "critical
        # thinking in the broadest context of technological change", verbatim.
        "technological change": 1.2, "emerging": 0.9, "evolution": 0.9,
        "next generation": 1.0, "upcoming": 0.9, "advancement": 0.9,
    },
}

# Specialization vocabulary: the nouns that say an outcome belongs to an
# engineering discipline at all. Traceable to PO1's own WK4 -- "engineering
# specialist knowledge that provides theoretical frameworks and bodies of
# knowledge for the accepted practice areas in the engineering discipline".
#
# These are scored SEPARATELY from the signals above and can never contribute
# toward a strong match. Naming the topic tells you the outcome is inside the
# discipline, which is a partial engineering-knowledge match; it does not tell
# you the outcome demonstrates engineering-knowledge competency. Folding them
# into the main score instead took the false-strong rate from 6.7% to 15.8% on
# held-out courses -- domain nouns stack, and four of them cleared the strong
# threshold on subject matter alone. Capping the sum was tried too and only
# reached 10.5%; the split below is what actually holds.
#
# The list is ECE- and CS-leaning because that is what the labelled corpus
# covers. It is a floor to extend per discipline, not a claim to completeness.
_SPECIALISATION: dict[str, dict[str, float]] = {
    "ENGINEERING_KNOWLEDGE": {
        "transmission": 0.45, "electromagnetic": 0.5, "wave": 0.4,
        "radar": 0.5, "antenna": 0.5, "protocol": 0.45, "radio": 0.45,
        "network": 0.4, "semiconductor": 0.5, "transistor": 0.5,
        "amplifier": 0.5, "modulation": 0.5, "frequency": 0.45,
        "voltage": 0.45, "spectrum": 0.45, "thermodynamic": 0.5,
        "logic": 0.4, "memory": 0.4, "coding": 0.4,
    },
}


# Which concept each PO code carries, per framework. Two frameworks, two
# maps -- v4 merges Engineer-and-Society with Environment-and-Sustainability
# into a single PO6, and shifts everything below it up one slot.
_PO_CONCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
    "GAPC_V3": {
        "PO1": ("ENGINEERING_KNOWLEDGE",), "PO2": ("PROBLEM_ANALYSIS",),
        "PO3": ("DESIGN_DEVELOPMENT",), "PO4": ("INVESTIGATION",),
        "PO5": ("TOOL_USAGE",), "PO6": ("ENGINEER_AND_WORLD",),
        "PO7": ("ENGINEER_AND_WORLD",), "PO8": ("ETHICS",),
        "PO9": ("TEAM_WORK",), "PO10": ("COMMUNICATION",),
        "PO11": ("PROJECT_MANAGEMENT",), "PO12": ("LIFELONG_LEARNING",),
    },
    "GAPC_V4": {
        "PO1": ("ENGINEERING_KNOWLEDGE",), "PO2": ("PROBLEM_ANALYSIS",),
        "PO3": ("DESIGN_DEVELOPMENT",), "PO4": ("INVESTIGATION",),
        "PO5": ("TOOL_USAGE",), "PO6": ("ENGINEER_AND_WORLD",),
        "PO7": ("ETHICS",), "PO8": ("TEAM_WORK",), "PO9": ("COMMUNICATION",),
        "PO10": ("PROJECT_MANAGEMENT",), "PO11": ("LIFELONG_LEARNING",),
    },
}

# Bloom verbs carry their own PO signal independent of subject matter.
_VERB_AFFINITY: dict[str, tuple[str, ...]] = {
    "define": ("ENGINEERING_KNOWLEDGE",), "list": ("ENGINEERING_KNOWLEDGE",),
    "identify": ("ENGINEERING_KNOWLEDGE", "PROBLEM_ANALYSIS"),
    "recall": ("ENGINEERING_KNOWLEDGE",), "explain": ("ENGINEERING_KNOWLEDGE",),
    "describe": ("ENGINEERING_KNOWLEDGE",), "classify": ("PROBLEM_ANALYSIS",),
    "summarize": ("ENGINEERING_KNOWLEDGE",),
    "apply": ("ENGINEERING_KNOWLEDGE",), "calculate": ("ENGINEERING_KNOWLEDGE",),
    "implement": ("DESIGN_DEVELOPMENT", "TOOL_USAGE"), "use": ("TOOL_USAGE",),
    "solve": ("PROBLEM_ANALYSIS", "ENGINEERING_KNOWLEDGE"),
    "demonstrate": ("TOOL_USAGE",),
    "analyze": ("PROBLEM_ANALYSIS",), "analyse": ("PROBLEM_ANALYSIS",),
    "compare": ("PROBLEM_ANALYSIS",), "differentiate": ("PROBLEM_ANALYSIS",),
    "examine": ("PROBLEM_ANALYSIS", "INVESTIGATION"),
    "evaluate": ("PROBLEM_ANALYSIS", "INVESTIGATION"),
    "justify": ("INVESTIGATION",), "assess": ("PROBLEM_ANALYSIS", "INVESTIGATION"),
    "critique": ("PROBLEM_ANALYSIS", "INVESTIGATION"),
    "interpret": ("PROBLEM_ANALYSIS",), "investigate": ("INVESTIGATION",),
    "design": ("DESIGN_DEVELOPMENT",), "develop": ("DESIGN_DEVELOPMENT",),
    "formulate": ("DESIGN_DEVELOPMENT", "PROBLEM_ANALYSIS"),
    "construct": ("DESIGN_DEVELOPMENT",),
    # "Present" only means Communication when it is the action. As a content
    # term it fired on "present day technology"; as a leading verb it is
    # unambiguous. See the COMMUNICATION lexicon.
    "present": ("COMMUNICATION",),
}

# One weight for any leading verb. Giving the first-position verb a heavier
# one was tried and dropped: at 1.7 a bare verb cleared the strong threshold
# with no subject-matter support at all, taking false strongs from 9% to 29%
# on the labelled corpus. Every value that fixed that scored no better than
# this one, so the split earns nothing and is not kept.
VERB_SIGNAL_WEIGHT = 1.0
# How much a leading verb still counts as subject matter, on top of its verb
# bonus. Eleven words sit in both tables, so 1.0 scores them twice.
#
# That looked like a bug and discarding it (0.0) took dev false strongs to 0%
# -- and strong recall from 83% to 47%, because the duplication was carrying
# most of the strong signal. What actually made the double count safe was the
# Bloom ceiling below, extended to problem analysis: 'Identify the rules and
# safety practices' is a K1 outcome and can no longer claim a strong analysis
# match however many tables list 'identify'. With that in place 1.0 beats
# every damped value on both splits, so the duplication stays and the ceiling
# does the work.
VERB_AS_CONTENT_DAMPING = 1.0
STRONG_THRESHOLD = 1.6
PARTIAL_THRESHOLD = 0.7

# ---- corroboration from the course's own syllabus ----------------------
#
# A course outcome is one sentence. The unit-wise syllabus beneath it in the
# same document is a page, and it is the only place that says what the course
# actually teaches. Digital System Design's CO4 reads "Analyze various logic
# families and design circuits using PLDs" -- nothing there evidences the
# modern-tool-usage PO. Unit IV of the same course names "Verilog RTL coding"
# and "Siemens EDA Precision RTL / Cadence Genus / Synopsys Design Compiler".
# That is real evidence, in the university's own words, and ignoring it was
# leaving the tool and investigation columns to be filled by convention alone.
#
# It is corroboration, never substitution, and three rules keep it that way:
#
#   1. Each syllabus term counts for a quarter of its weight. The syllabus is
#      a page of subject matter and matches far more terms than a sentence
#      does; at full weight it would drown the outcome it is meant to support.
#   2. The total is capped below PARTIAL_THRESHOLD, so no amount of syllabus
#      text can map a PO on its own. Arithmetic, not policy.
#   3. It is only consulted once the outcome's own text already points at the
#      PO (SYLLABUS_MIN_STATEMENT). A course that teaches simulation does not
#      make every one of its outcomes a tool-usage outcome.
#
# Promotion to STRONG additionally needs the statement alone to have reached
# PARTIAL_THRESHOLD, so the sentence carries the claim and the syllabus only
# confirms it. The matched syllabus terms are reported separately in the
# evidence, so a reader can see which half of the case came from where.
SYLLABUS_TERM_WEIGHT = 0.25
SYLLABUS_CAP = 0.6                 # < PARTIAL_THRESHOLD, deliberately
# 0.5, not 0.3. At 0.3 a single incidental weak word admitted the whole
# course syllabus: "basic" (0.4) in a human-values outcome pulled in five
# generic terms from the unit text and proposed Engineering Knowledge against
# a course on the Self and the Body. The lightest term in any lexicon weighs
# 0.35, so 0.5 means the outcome has to carry either a mid-weight term or two
# weak ones before course content is allowed to corroborate anything -- which
# is what "already points at this PO" was always meant to mean.
SYLLABUS_MIN_STATEMENT = 0.5


@dataclass(frozen=True)
class Proposal:
    po_code: str
    competency: Competency
    score: float
    matched_terms: list[str]
    from_verb: bool
    # Terms found in the course's unit-wise syllabus rather than in this
    # outcome's own sentence. Kept apart so the evidence bundle can say which
    # half of the case came from where.
    syllabus_terms: list[str] = field(default_factory=list)
    syllabus_score: float = 0.0

    @property
    def evidence(self) -> str:
        if not self.matched_terms and not self.syllabus_terms:
            return "No matching terms."
        parts = []
        if self.matched_terms:
            src = "action verb" if self.from_verb else "content terms"
            parts.append(f"Matched {src}: {', '.join(self.matched_terms)}.")
        if self.syllabus_terms:
            parts.append(
                f"Corroborated by the course syllabus: "
                f"{', '.join(self.syllabus_terms)}.")
        return " ".join(parts)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9\s-]", " ", text.lower())


def _leading_verbs(text: str) -> list[str]:
    """
    The verb(s) that *open* the outcome, and only those.

    Scanning the first six words read subject matter as the action: "Perform
    layout design for physical analysis of MOS transistors" found "design"
    and awarded the design PO a verb bonus, when the outcome's actual verb is
    "perform" and its subject is a layout. That single mistake produced most
    of the false strongs on the labelled corpus.

    Conjoined openers all count -- "Design and analyze various circuits"
    genuinely claims both.
    """
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return []
    i = 1 if words[0] == "to" else 0
    out: list[str] = []
    while i < len(words):
        w = words[i]
        if w in _VERB_AFFINITY:
            out.append(w)
            i += 1
        elif w in ("and", "or") and out:
            i += 1
        elif not out and w in _NON_VERB_OPENERS:
            i += 1                     # "Apply the knowledge of X to ..."
        else:
            break
    return out


# Words that may sit before the real verb without being subject matter.
_NON_VERB_OPENERS = frozenset({"be", "able", "students", "student", "will", "can"})


@lru_cache(maxsize=512)
def _term_re(term: str) -> re.Pattern[str]:
    """
    A lexicon term matches at the START of a word, never inside one.

    Every entry is either a word-initial stem ("communicat", "sustainab") or a
    phrase, so anchoring the left edge keeps all of them while dropping the
    accidents. Plain substring matching fired `present` (Communication) on
    "how to REPRESENT drone systems" and on "PRESENT day technology", and
    `test` (Investigation) on "latest" and "greatest" -- two spurious partial
    mappings in a single real syllabus. The right edge stays open on purpose:
    "communicat" has to reach communicate, communication and communicating.
    """
    return re.compile(r"(?<![a-z])" + re.escape(term))


def _score_concept(text: str, concept: str,
                   exclude: frozenset[str] = frozenset()) -> tuple[float, list[str]]:
    """
    Score the subject matter. `exclude` holds the outcome's leading verbs.

    Eleven words -- identify, analyze, compare, design, implement among them --
    sit in both the content lexicon and the verb-affinity table, so an outcome
    opening with one scored it twice and cleared the strong threshold on a
    single word. "Identify the rules and safety practices for drone operation"
    became a strong problem-analysis match on nothing but the word "identify".
    A verb is one piece of evidence however many tables list it.
    """
    return _score_terms(text, _PO_SIGNALS[concept], exclude)


def _score_terms(text: str, terms: dict[str, float],
                 exclude: frozenset[str] = frozenset()) -> tuple[float, list[str]]:
    score, matched = 0.0, []
    for term, weight in terms.items():
        if _term_re(term).search(text):
            score += weight * (VERB_AS_CONTENT_DAMPING if term in exclude else 1.0)
            matched.append(term)
    return score, matched


def propose_for_co(
    statement: str,
    *,
    framework_code: str,
    blooms_levels: list[int] | None = None,
    syllabus: str = "",
) -> list[Proposal]:
    """
    Propose a competency for every PO in the framework. Returns only POs with
    a non-NONE proposal, strongest first.

    `syllabus` is the course's unit-wise content from the same document. It
    corroborates the outcome's own evidence and can never replace it -- see
    SYLLABUS_CAP above. Omit it and this function behaves exactly as before,
    which is what the accuracy evaluation runs against.
    """
    concepts = _PO_CONCEPTS.get(framework_code)
    if concepts is None:
        raise ValueError(f"No competency signals defined for framework {framework_code!r}")

    text = _normalise(statement)
    syl = _normalise(syllabus) if syllabus.strip() else ""
    verbs = _leading_verbs(statement)
    verb_concepts: dict[str, str] = {}
    for v in verbs:
        for concept in _VERB_AFFINITY.get(v, ()):
            verb_concepts.setdefault(concept, v)

    out: list[Proposal] = []
    for po_code, po_concepts in concepts.items():
        score, matched, from_verb = 0.0, [], False
        spec = 0.0
        syl_score, syl_matched = 0.0, []
        for concept in po_concepts:
            s, m = _score_concept(text, concept, exclude=frozenset(verbs))
            score += s
            matched.extend(m)
            if concept in _SPECIALISATION:
                s2, m2 = _score_terms(text, _SPECIALISATION[concept])
                spec += s2
                matched.extend(m2)
            if concept in verb_concepts:
                v = verb_concepts[concept]
                score += VERB_SIGNAL_WEIGHT
                from_verb = True
                matched.append(f"verb '{v}'")
            if syl:
                s3, m3 = _score_terms(syl, _PO_SIGNALS[concept])
                syl_score += s3 * SYLLABUS_TERM_WEIGHT
                syl_matched.extend(m3)

        # The syllabus corroborates an outcome that already points at this PO;
        # it never speaks for one that does not. Capped below the partial
        # threshold, so course content alone can map nothing.
        if score < SYLLABUS_MIN_STATEMENT:
            syl_score, syl_matched = 0.0, []
        syl_score = min(syl_score, SYLLABUS_CAP)

        # Only the competency signals can reach STRONG. Specialization nouns
        # join in for the partial test, where "this outcome is about radar"
        # is exactly the evidence wanted.
        if score >= STRONG_THRESHOLD:
            comp = Competency.STRONG
        elif score >= PARTIAL_THRESHOLD and score + syl_score >= STRONG_THRESHOLD:
            # The sentence carries the claim, the syllabus confirms it.
            comp = Competency.STRONG
        elif score + spec + syl_score >= PARTIAL_THRESHOLD:
            comp = Competency.PARTIAL
        else:
            continue

        # A recall-level outcome cannot credibly claim a strong match on the
        # design/investigation/tool POs -- the plan's hard ceiling rule.
        if blooms_levels and comp is Competency.STRONG:
            if action_level_for_bloom(blooms_levels) == 1 and any(
                c in ("DESIGN_DEVELOPMENT", "INVESTIGATION", "TOOL_USAGE",
                      "PROBLEM_ANALYSIS")
                for c in po_concepts
            ):
                comp = Competency.PARTIAL
                matched.append("capped: recall-level outcome")

        out.append(Proposal(po_code, comp, round(score, 2),
                            sorted(set(matched)), from_verb,
                            sorted(set(syl_matched)), round(syl_score, 2)))

    out.sort(key=lambda p: (-p.score, p.po_code))
    return out


def signal_strength(
    statement: str,
    *,
    framework_code: str,
    syllabus: str = "",
) -> dict[str, float]:
    """
    Every PO this outcome shows *any* trace of, and how much, thresholds off.

    `propose_for_co` answers "is there enough here to map?". This answers the
    weaker question "is there anything here at all?", which is what a
    departmental convention needs before it fills a column. The department's
    instruction was "it is not necessary to fill all the columns up to PO5 --
    if they really need to be filled then fill them, else let them be": a
    convention that asserts a column for every outcome regardless is a
    blanket, and this is the test that stops it being one.

    Deliberately a separate, cheap pass rather than a mode of `propose_for_co`:
    nothing here can change a proposal, so the measured accuracy of the
    inference path cannot move because of it.
    """
    concepts = _PO_CONCEPTS.get(framework_code)
    if concepts is None:
        raise ValueError(f"No competency signals defined for framework {framework_code!r}")

    text = _normalise(statement)
    syl = _normalise(syllabus) if syllabus.strip() else ""
    verbs = _leading_verbs(statement)
    verb_concepts = {c for v in verbs for c in _VERB_AFFINITY.get(v, ())}

    out: dict[str, float] = {}
    for po_code, po_concepts in concepts.items():
        total = 0.0
        for concept in po_concepts:
            total += _score_concept(text, concept, exclude=frozenset(verbs))[0]
            if concept in _SPECIALISATION:
                total += _score_terms(text, _SPECIALISATION[concept])[0]
            if concept in verb_concepts:
                total += VERB_SIGNAL_WEIGHT
            if syl:
                total += _score_terms(syl, _PO_SIGNALS[concept])[0] * SYLLABUS_TERM_WEIGHT
        if total > 0:
            out[po_code] = round(total, 2)
    return out


def propose_matrix(
    cos: dict[str, tuple[str, list[int]]],
    *,
    framework_code: str,
    max_pos_per_co: int = 6,
    syllabus: str = "",
) -> dict[str, dict[str, int]]:
    """
    Propose the whole competency matrix: {co_code: {po_code: competency}}.
    Caps each CO at the strongest `max_pos_per_co` proposals, since raw
    signal matching over-maps and the SAR's accepted range is 3-6 POs per CO.

    `syllabus` is the whole course's unit content, shared by every outcome in
    it -- the same block corroborates each one, since it describes the course
    rather than any single outcome.
    """
    matrix: dict[str, dict[str, str]] = {}
    for co_code, (statement, blooms) in cos.items():
        proposals = propose_for_co(
            statement, framework_code=framework_code, blooms_levels=blooms,
            syllabus=syllabus,
        )[:max_pos_per_co]
        matrix[co_code] = {p.po_code: int(p.competency) for p in proposals}
    return matrix
