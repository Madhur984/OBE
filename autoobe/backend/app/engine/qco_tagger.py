"""
Question -> CO tagging: read an exam question, say which outcome it assesses.

Tagging a question paper by hand is ~30 minutes per exam and it is the step
faculty most often skip or fake, which quietly corrupts every attainment
number downstream. This does it automatically.

Two layers, in this order:

  1. A deterministic scorer (always on, no key, no network). Content-word
     overlap against each CO statement, plus Bloom-verb alignment. It reports
     a confidence from the margin between the best and second-best CO.
  2. Optional LLM arbitration, used ONLY where layer 1 is genuinely unsure.
     Constrained to choosing among the course's own COs, temperature 0, forced
     JSON, and the model/version is recorded on every result.

The order matters and is not an implementation detail. Layer 1 handles the
clear majority of questions, runs offline, and is reproducible years later
during a re-accreditation audit. Layer 2 exists for the genuinely ambiguous
tail, and its answers are marked as model-assisted so a reviewer can see
exactly which tags came from where.

Nothing here writes a number. A tag decides which CO a question's marks count
toward, so it is reviewed and confirmed by faculty before attainment runs --
the same rule the mapping engine follows.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field

from app.engine.blooms import BLOOM_LEVEL_VERBS

# Words carrying no subject signal. Kept deliberately small: over-filtering
# throws away real technical vocabulary.
_STOP = frozenset("""
a an the and or but if then than that this these those of in on at to for with
from by as is are was were be been being do does did what which who whom whose
how why when where write explain discuss describe give state list define draw
short note notes following any all each both some such it its their there here
question attempt marks answer section part figure diagram example consider
using use used shall will can may must also more most other another same
""".split())

_TOKEN = re.compile(r"[a-z][a-z0-9+#.-]{1,}")
_VERBS = {v: lvl for lvl, (_, verbs) in BLOOM_LEVEL_VERBS.items() for v in verbs}

# Exam questions use acronyms where outcome statements spell the term out --
# a question says "ER diagram" while the CO says "entity-relationship
# modelling". Without this, a question matches the wrong outcome *confidently*,
# which is worse than not matching at all. Expansion runs on both sides, so
# the comparison is like for like.
_EXPANSIONS: dict[str, str] = {
    "er": "entity relationship", "erd": "entity relationship diagram",
    "dbms": "database management system", "rdbms": "relational database management system",
    "sql": "sql query structured", "pl": "procedural language",
    "os": "operating system", "cpu": "processor scheduling",
    "fcfs": "scheduling first come first served", "sjf": "scheduling shortest job",
    "rr": "round robin scheduling", "lru": "page replacement least recently used",
    "fifo": "first in first out", "tlb": "translation lookaside buffer memory",
    "ipc": "inter process communication", "dma": "direct memory access",
    "dp": "dynamic programming", "bfs": "breadth first search graph traversal",
    "dfs": "depth first search graph traversal", "mst": "minimum spanning tree graph",
    "np": "nondeterministic polynomial complexity", "adt": "abstract data type",
    "ai": "artificial intelligence", "ml": "machine learning",
    "ann": "artificial neural network", "svm": "support vector machine",
    "psk": "phase shift keying modulation", "fsk": "frequency shift keying modulation",
    "ask": "amplitude shift keying modulation", "qam": "quadrature amplitude modulation",
    "pcm": "pulse code modulation", "snr": "signal noise ratio",
    "ber": "bit error rate", "fft": "fast fourier transform",
    "osi": "open systems interconnection network layer",
    "tcp": "transmission control protocol network", "udp": "user datagram protocol network",
    "http": "hypertext transfer protocol web", "ip": "internet protocol addressing",
    "html": "hypertext markup language web", "xml": "extensible markup language",
    "api": "application programming interface", "ui": "user interface",
    "srs": "software requirements specification", "sdlc": "software development life cycle",
    "uml": "unified modeling language", "oop": "object oriented programming",
    "ic": "integrated circuit", "vlsi": "very large scale integration",
    "fsm": "finite state machine", "alu": "arithmetic logic unit",
    "emf": "electromotive force", "dc": "direct current", "ac": "alternating current",
    "cad": "computer aided design tool", "fem": "finite element method",
    "cfd": "computational fluid dynamics", "hvac": "heating ventilation air conditioning",
    "bod": "biochemical oxygen demand", "cod": "chemical oxygen demand",
    "sdg": "sustainable development goal",
}

# Confidence bands. Tuned so "review" is the default posture for anything the
# scorer cannot separate cleanly.
#
# This band is calibrated *against* the scoring weights below and has to be
# re-swept whenever they change. Changing the normalisation without re-tuning
# the margin quietly raised coverage and dropped precision on high-confidence
# tags -- the scorer got better while its self-assessment got worse, which is
# the worst combination available.
HIGH_MARGIN = 0.35
LOW_SCORE = 0.10

# Scoring weights, exposed as constants so `tagger_eval` can sweep them
# against the labelled corpus rather than having them tuned by intuition.
# Every value here was chosen on the corpus's dev split and then measured
# once on holdout; see tests/eval/qco_corpus.json for why that split exists.
VERB_BONUS_EXACT = 0.18     # question's command verb sits at the CO's level
VERB_BONUS_BELOW = 0.06     # question sits below the CO's level
# How much the outcome's own length discounts a match. 0.0 is pure cosine
# (length-neutral); 1.0 is coverage-of-the-outcome, which rewards terse
# outcomes -- a generic "Explain the structure of X" competes for questions
# belonging to every other outcome in the course.
#
# A worked negative result, kept because it is the useful part. Sweeping this
# and the verb bonus on the dev split produced a large dev improvement
# (silent errors 15.0% -> 5.0%) that did not survive contact with holdout,
# where the same settings made things worse (8.5% -> 12.8%). The tuned values
# had fitted the vocabulary of two CS courses. Reverting them and keeping only
# the confidence recalibration -- one parameter, not three -- gave 9.2% silent
# on the whole corpus with materially better coverage than the tuned version.
#
# Left parameterised rather than inlined so the sweep is repeatable when the
# corpus grows; with 87 questions across six courses it is too small to
# support fitting three weights, and pretending otherwise is how a scorer ends
# up tuned to its own test set.
LENGTH_BIAS = 1.0


@dataclass(frozen=True)
class COScore:
    co_code: str
    score: float
    overlap: list[str]
    verb_match: bool


@dataclass
class QuestionTag:
    question_no: str
    question_text: str
    co_code: str | None
    confidence: str                    # "high" | "review" | "unsure"
    method: str                        # "deterministic" | "llm-arbitrated"
    scores: list[COScore] = field(default_factory=list)
    rationale: str = ""
    model: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.confidence != "high"


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        # The token pattern allows '.' and '-' so "node.js" and "two-phase"
        # survive intact, which means sentence-ending punctuation rides along
        # too: "schema." then never matched "schema" and silently decided tags.
        raw = raw.strip(".-")
        if not raw:
            continue
        expansion = _EXPANSIONS.get(raw)
        if expansion:
            out.append(raw)
            out.extend(expansion.split())
            continue
        if raw not in _STOP and len(raw) > 2:
            out.append(raw)
        # Outcome statements hyphenate what questions write as two words or an
        # acronym: "entity-relationship" vs "ER" vs "entity relationship".
        # Emitting the parts as well makes all three comparable.
        if "-" in raw:
            out.extend(p for p in raw.split("-") if len(p) > 2 and p not in _STOP)
    return out


# Longest first: "serializability" must strip "ability", not "s".
_SUFFIXES = (
    "ibility", "ability", "isation", "ization",
    "ations", "ising", "izing", "ation",
    "ized", "ised", "izes", "ises", "ible", "able",
    "ers", "ing", "ies", "ize", "ise", "ed", "es", "s",
)


def _stem(word: str) -> str:
    """
    A crude suffix strip -- enough to match normalise/normalised/normalisation.

    Applied to a fixed point rather than once, because a single pass leaves
    inflections of the same word in different places: "processes" -> "process"
    but "process" -> "proces", so the two never match. Iterating until nothing
    more strips makes the function idempotent, which is the property that
    actually matters here.

    Outcomes name the property ("serializability") while questions name the
    test ("is S serializable?"); these must collapse to one stem or the
    question matches nothing at all.
    """
    for _ in range(3):
        before = word
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                # "modelling" -> "modell" would not match "model"; collapse the
                # doubled consonant English adds before -ing/-ed.
                if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiou":
                    word = word[:-1]
                break
        if word == before:
            break
    # A trailing silent 'e' keeps base forms apart from their participles:
    # "page"/"paging" and "schedule"/"scheduling" are the same word to a
    # question paper, and were scoring as different ones.
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _leading_verb(text: str) -> tuple[str | None, int | None]:
    for w in re.findall(r"[a-z]+", text.lower())[:4]:
        if w in _VERBS:
            return w, _VERBS[w]
    return None, None


def score_question(
    question_text: str,
    cos: dict[str, tuple[str, list[int]]],
) -> list[COScore]:
    """
    Score a question against every CO. `cos` maps co_code to
    (statement, blooms_levels).
    """
    q_tokens = _tokens(question_text)
    if not q_tokens:
        return []
    q_stems = {_stem(t) for t in q_tokens}
    q_verb, q_level = _leading_verb(question_text)

    # Weight terms by how few outcomes contain them. Within one course, words
    # like "system" or "database" appear in most outcomes and separate nothing,
    # while "serializability" or "normalization" identify an outcome on their
    # own. Counting matches unweighted let generic vocabulary decide the tag.
    co_stems: dict[str, set[str]] = {}
    for code, (statement, _) in cos.items():
        co_stems[code] = {_stem(t) for t in _tokens(statement)}
    n_cos = max(1, len(co_stems))
    doc_freq: dict[str, int] = {}
    for stems in co_stems.values():
        for s in stems:
            doc_freq[s] = doc_freq.get(s, 0) + 1

    def idf(stem: str) -> float:
        # +0.35 floor so a term in every outcome still counts a little.
        return 0.35 + math.log(1 + n_cos / (1 + doc_freq.get(stem, 0)))

    out: list[COScore] = []
    for code, (statement, blooms) in cos.items():
        c_stems = co_stems[code]
        if not c_stems:
            out.append(COScore(code, 0.0, [], False))
            continue

        shared = q_stems & c_stems
        numerator = sum(idf(s) for s in shared)

        # LENGTH_BIAS interpolates between dividing by the outcome's own
        # weight mass (1.0, the default) and cosine normalisation (0.0). See
        # the constant for why the default stayed where it is.
        q_mass = sum(idf(s) for s in q_stems) or 1.0
        c_mass = sum(idf(s) for s in c_stems) or 1.0
        denominator = (c_mass ** LENGTH_BIAS) * ((q_mass * c_mass) ** ((1 - LENGTH_BIAS) / 2))
        overlap = numerator / (denominator or 1.0)

        verb_match = False
        if q_level is not None and blooms:
            # A question's command verb should sit at or below the CO's level.
            # Weak evidence on its own -- nearly every course writes its CO1 at
            # K2 with "Explain" -- so this breaks ties rather than deciding tags.
            if q_level in blooms:
                overlap += VERB_BONUS_EXACT
                verb_match = True
            elif q_level <= max(blooms):
                overlap += VERB_BONUS_BELOW

        readable = sorted(
            {t for t in q_tokens if _stem(t) in shared},
            key=lambda w: (-idf(_stem(w)), w),
        )[:6]
        out.append(COScore(code, round(min(overlap, 1.0), 4), readable, verb_match))

    out.sort(key=lambda s: (-s.score, s.co_code))
    return out


def _confidence(scores: list[COScore]) -> str:
    if not scores or scores[0].score < LOW_SCORE:
        return "unsure"
    best = scores[0].score
    second = scores[1].score if len(scores) > 1 else 0.0
    margin = (best - second) / best if best else 0.0
    return "high" if margin >= HIGH_MARGIN else "review"


def tag_question(
    question_no: str,
    question_text: str,
    cos: dict[str, tuple[str, list[int]]],
) -> QuestionTag:
    """Deterministic tagging. Always available; no network, no key."""
    scores = score_question(question_text, cos)
    if not scores:
        return QuestionTag(question_no, question_text, None, "unsure",
                           "deterministic", [], "No usable words in the question.")

    conf = _confidence(scores)
    best = scores[0]
    if conf == "unsure":
        rationale = (
            "No course outcome shares enough subject vocabulary with this "
            "question to call it."
        )
        return QuestionTag(question_no, question_text, None, conf,
                           "deterministic", scores, rationale)

    terms = ", ".join(best.overlap) or "no distinctive terms"
    rationale = f"Shares {terms} with {best.co_code}"
    if best.verb_match:
        rationale += "; the command verb matches its Bloom level"
    if conf == "review" and len(scores) > 1:
        rationale += f". {scores[1].co_code} scores closely, so confirm this one"
    return QuestionTag(question_no, question_text, best.co_code, conf,
                       "deterministic", scores, rationale + ".")


def tag_paper(
    questions: list[tuple[str, str]],
    cos: dict[str, tuple[str, list[int]]],
) -> list[QuestionTag]:
    """Tag a whole paper: [(question_no, question_text), ...]."""
    return [tag_question(no, text, cos) for no, text in questions]


# --------------------------------------------------------------- LLM layer

def llm_available() -> bool:
    """True when an LLM can actually be called, so the UI can say so plainly."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def llm_status() -> dict:
    try:
        import anthropic  # noqa: F401,PLC0415
        sdk = True
    except ImportError:
        sdk = False
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if sdk and key:
        detail = "Ready. Ambiguous questions will be sent for arbitration."
    elif not sdk and not key:
        detail = ("Not configured. Install the `anthropic` package and set "
                  "ANTHROPIC_API_KEY to enable arbitration.")
    elif not sdk:
        detail = "ANTHROPIC_API_KEY is set, but the `anthropic` package is not installed."
    else:
        detail = "The `anthropic` package is installed, but ANTHROPIC_API_KEY is not set."
    return {"available": sdk and key, "sdk_installed": sdk,
            "api_key_set": key, "detail": detail,
            "model": os.environ.get("AUTOOBE_LLM_MODEL", DEFAULT_MODEL)}


# Pinned deliberately. The plan requires the model version to be recorded per
# academic year so a re-accreditation audit can reproduce the same tags years
# later -- override with AUTOOBE_LLM_MODEL, and record what was used.
DEFAULT_MODEL = "claude-sonnet-5"

_SYSTEM = (
    "You map university exam questions to the course outcome each one assesses. "
    "You will be given the course's outcomes and one question. Choose exactly "
    "one outcome from the list, or null if none genuinely fits. Judge by what "
    "the student must actually do to answer, not by surface keyword overlap. "
    "Never invent an outcome code that is not in the list."
)


def _build_prompt(question_text: str, cos: dict[str, tuple[str, list[int]]],
                  scores: list[COScore]) -> str:
    lines = ["Course outcomes:"]
    for code, (statement, blooms) in cos.items():
        k = f" (Bloom K{','.join(map(str, blooms))})" if blooms else ""
        lines.append(f"  {code}{k}: {statement}")
    lines.append("")
    lines.append(f"Question: {question_text}")
    if scores:
        top = ", ".join(f"{s.co_code}={s.score:.2f}" for s in scores[:3])
        lines.append("")
        lines.append(
            f"A keyword scorer ranked these highest: {top}. It could not "
            f"separate them confidently. Use it as a hint only."
        )
    lines.append("")
    lines.append(
        'Reply with JSON only: {"co_code": "<code or null>", '
        '"confidence": "high|medium|low", "reason": "<one sentence, max 25 words>"}'
    )
    return "\n".join(lines)


def arbitrate_with_llm(
    tag: QuestionTag,
    cos: dict[str, tuple[str, list[int]]],
    *,
    model: str | None = None,
    timeout: float = 20.0,
) -> QuestionTag:
    """
    Ask a model to settle a tag the deterministic scorer could not.

    Returns the original tag unchanged on any failure -- missing key, network
    error, malformed reply, or a code outside the course's own outcomes. An
    arbitration layer that can fall over must never take the pipeline with it.
    """
    if not llm_available():
        return tag

    import anthropic  # noqa: PLC0415

    model = model or os.environ.get("AUTOOBE_LLM_MODEL", DEFAULT_MODEL)
    try:
        client = anthropic.Anthropic(timeout=timeout)
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            temperature=0,               # reproducibility matters more than variety
            system=_SYSTEM,
            messages=[{"role": "user",
                       "content": _build_prompt(tag.question_text, cos, tag.scores)}],
        )
        raw = "".join(block.text for block in resp.content
                      if getattr(block, "type", "") == "text").strip()
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return tag
        data = json.loads(match.group(0))
    except Exception:
        return tag                       # degrade to the deterministic answer

    code = data.get("co_code")
    if code is not None and code not in cos:
        return tag                       # hallucinated code: reject, keep layer 1

    reason = str(data.get("reason", "")).strip()[:200]
    conf_map = {"high": "high", "medium": "review", "low": "unsure"}
    confidence = conf_map.get(str(data.get("confidence", "")).lower(), "review")

    tag.co_code = code
    tag.confidence = confidence
    tag.method = "llm-arbitrated"
    tag.model = model
    tag.rationale = reason or tag.rationale
    return tag


def tag_paper_smart(
    questions: list[tuple[str, str]],
    cos: dict[str, tuple[str, list[int]]],
    *,
    use_llm: bool = True,
    model: str | None = None,
) -> list[QuestionTag]:
    """
    Tag a paper, escalating only the questions the scorer could not settle.

    Escalating everything would be slower, cost more, and -- most importantly --
    replace reproducible answers with model-dependent ones for questions that
    were never in doubt.
    """
    tags = tag_paper(questions, cos)
    if not use_llm or not llm_available():
        return tags
    for tag in tags:
        if tag.needs_review:
            arbitrate_with_llm(tag, cos, model=model)
    return tags


def split_question_paper(text: str) -> list[tuple[str, str]]:
    """
    Split raw question-paper text into (number, text) pairs.

    Handles the numbering AKTU papers actually use: "1.", "Q1", "Q.1",
    "1 (a)", "(a)" continuing the previous number.
    """
    pattern = re.compile(
        r"^\s*(?:Q\s*\.?\s*)?(\d{1,2})\s*[.)\]]?\s*(\([a-z]\))?\s+(?=\S)|"
        r"^\s*(\([a-z]\))\s+(?=\S)",
        re.I | re.M,
    )
    out: list[tuple[str, str]] = []
    matches = list(pattern.finditer(text))
    if not matches:
        stripped = text.strip()
        return [("Q1", stripped)] if stripped else []

    last_number = None
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = re.sub(r"\s+", " ", text[start:end]).strip()
        body = re.sub(r"\[\s*\d+\s*(marks?)?\s*\]$", "", body, flags=re.I).strip()
        if len(body) < 8:
            continue
        if m.group(1):
            last_number = m.group(1)
            label = f"Q{last_number}{(m.group(2) or '').strip('()')}"
        else:
            part = (m.group(3) or "").strip("()")
            label = f"Q{last_number or 1}{part}"
        out.append((label, body))
    return out
