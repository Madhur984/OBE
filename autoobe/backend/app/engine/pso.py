"""
Program Specific Outcomes: quality checks and CO -> PSO inference.

PSOs are the one outcome column that cannot be seeded. POs come from a
national framework and are identical in every college in India; PSOs are
written by the department and are different in every one. That is precisely
what makes them awkward for automation -- there is no lexicon to ship.

The way out is that a PSO set is self-describing. Two or three PSOs written
for one program share the generic engineering vocabulary and differ in the
domain vocabulary, so weighting each term by how *few* PSOs contain it
isolates what actually distinguishes PSO1 from PSO2. That is the same
inverse-document-frequency idea the question tagger uses, applied to a much
smaller document set, and it means inference works the moment a department
types its PSOs -- no configuration, no training.

A caveat stated plainly: with only two or three documents, IDF is a weak
statistic. So the thresholds here are set to under-propose, matching
competency.py, and every proposal carries the terms that fired.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.engine.blooms import action_level_for_bloom
from app.engine.co_quality import GOOD_VERBS
from app.engine.mapping_rules import Competency, map_co_to_po
from app.engine.qco_tagger import _stem, _tokens

# Vocabulary that appears in the NBA PO statements themselves. A PSO built
# only from these words is a restatement of a PO, which is the single most
# common finding a peer team raises about PSOs -- so it is checked for.
_GENERIC_PO_TERMS = frozenset(_stem(w) for w in """
engineering knowledge mathematics science fundamental principle complex problem
analysis design development solution investigation experiment research tool
modern technique society societal environment sustainability ethics ethical
professional responsibility team individual leader communication project
management finance life-long lifelong learning technology system apply analyze
analyse evaluate identify formulate solve conduct function effectively
appropriate consideration need context specialization
""".split())

# Below this share of distinctive vocabulary a PSO reads as PO boilerplate.
SPECIFICITY_FLOOR = 0.25

STRONG_THRESHOLD = 0.34
PARTIAL_THRESHOLD = 0.16

# NBA's SAR tables have room for a small PSO set, and departments that write
# more than four end up with PSOs no course can map to. Advisory, not a rule.
TYPICAL_PSO_RANGE = (2, 4)


@dataclass(frozen=True)
class PSO:
    code: str
    statement: str

    @property
    def action_level(self) -> int:
        return pso_action_level(self.statement)


@dataclass(frozen=True)
class PSOFinding:
    check: str
    severity: str          # error | warning | info
    message: str


@dataclass(frozen=True)
class PSOReport:
    code: str
    statement: str
    findings: list[PSOFinding]
    distinctive_terms: list[str]
    specificity: float

    @property
    def is_usable(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


@dataclass(frozen=True)
class PSOProposal:
    pso_code: str
    competency: Competency
    score: float
    matched_terms: list[str]

    @property
    def evidence(self) -> str:
        if not self.matched_terms:
            return "No matching terms."
        return f"Matched distinctive terms: {', '.join(self.matched_terms)}."


def pso_action_level(statement: str) -> int:
    """
    The PSO's own action level, for Rule 2. Derived from its leading Bloom
    verb, defaulting to 2 when no verb is recognised.

    Same standing as the POs' `default_action_level`: an editorial default,
    not something NBA prescribes. A department that disagrees should set it.
    """
    for w in re.findall(r"[a-z]+", statement.lower())[:6]:
        if w in GOOD_VERBS:
            return min(3, max(1, action_level_for_bloom([GOOD_VERBS[w]])))
    return 2


def _content_stems(statement: str) -> list[str]:
    return [_stem(t) for t in _tokens(statement)]


def check_pso(code: str, statement: str, *, siblings: list[str] | None = None) -> PSOReport:
    """
    Quality-check one PSO. `siblings` are the other PSO statements in the
    program, used to detect a pair that says the same thing twice.
    """
    findings: list[PSOFinding] = []
    text = statement.strip()
    if not text:
        return PSOReport(code, statement,
                         [PSOFinding("Content", "error", "The PSO is empty.")], [], 0.0)

    stems = _content_stems(text)
    distinctive = sorted({s for s in stems if s not in _GENERIC_PO_TERMS})
    specificity = (len(distinctive) / len(set(stems))) if stems else 0.0

    # The check that matters: is this actually program-*specific*?
    if specificity < SPECIFICITY_FLOOR:
        findings.append(PSOFinding(
            "Specific", "error",
            "This reads as a restatement of a Program Outcome rather than a "
            "Program Specific Outcome — almost all of its vocabulary is generic "
            "engineering language. Name the domain, the tools, or the class of "
            "systems that distinguishes this program from any other.",
        ))

    verb = next((w for w in re.findall(r"[a-z]+", text.lower())[:6] if w in GOOD_VERBS), None)
    if verb is None:
        findings.append(PSOFinding(
            "Action", "warning",
            "No measurable action verb near the start. A PSO is assessed like a "
            "CO, so it needs an observable verb (Apply, Design, Analyze, Develop).",
        ))

    words = len(text.split())
    if words < 6:
        findings.append(PSOFinding(
            "Content", "warning",
            "Very short — it may not be clear what the graduate can actually do.",
        ))
    elif words > 60:
        findings.append(PSOFinding(
            "Specific", "warning",
            f"{words} words. Long PSOs usually bundle several outcomes, which "
            f"makes them impossible to attain cleanly. Consider splitting.",
        ))

    for other in (siblings or []):
        other_stems = {s for s in _content_stems(other) if s not in _GENERIC_PO_TERMS}
        if not other_stems or not distinctive:
            continue
        overlap = len(set(distinctive) & other_stems) / len(set(distinctive) | other_stems)
        if overlap > 0.6:
            findings.append(PSOFinding(
                "Distinct", "warning",
                "Substantially overlaps another PSO in this program. Two PSOs that "
                "share their distinctive vocabulary will map to the same courses "
                "and cannot be told apart in Table 1.5.1.",
            ))
            break

    return PSOReport(code, text, findings, distinctive, round(specificity, 2))


def check_pso_set(psos: list[PSO]) -> tuple[list[PSOReport], list[PSOFinding]]:
    """Check each PSO, plus findings that only make sense across the set."""
    statements = [p.statement for p in psos]
    reports = [
        check_pso(p.code, p.statement,
                  siblings=[s for j, s in enumerate(statements) if j != i])
        for i, p in enumerate(psos)
    ]
    set_findings: list[PSOFinding] = []
    lo, hi = TYPICAL_PSO_RANGE
    if not psos:
        set_findings.append(PSOFinding(
            "Set", "error",
            "No PSOs are defined. SAR Tables 1.4.2.1, 1.5.1 and 3.8.1 all carry "
            "PSO columns, and leaving them empty is an automatic finding.",
        ))
    elif len(psos) < lo:
        set_findings.append(PSOFinding(
            "Set", "warning",
            f"Only {len(psos)} PSO defined; programs normally state {lo}–{hi}.",
        ))
    elif len(psos) > hi:
        set_findings.append(PSOFinding(
            "Set", "info",
            f"{len(psos)} PSOs defined. Beyond {hi}, some usually end up with no "
            f"course mapping to them, which is itself a finding.",
        ))
    return reports, set_findings


def _idf_weights(psos: list[PSO]) -> dict[str, float]:
    """
    Weight each stem by how few PSOs contain it. A term in every PSO tells you
    nothing about which one a CO belongs to.
    """
    n = len(psos)
    doc_freq: dict[str, int] = {}
    for p in psos:
        for s in set(_content_stems(p.statement)):
            doc_freq[s] = doc_freq.get(s, 0) + 1
    weights: dict[str, float] = {}
    for stem, df in doc_freq.items():
        w = math.log(1 + n / df)
        # Generic PO vocabulary is real language, not noise, but it must not
        # decide a mapping -- so it is damped rather than dropped.
        if stem in _GENERIC_PO_TERMS:
            w *= 0.3
        weights[stem] = w
    return weights


def propose_co_pso(
    co_statement: str,
    psos: list[PSO],
    *,
    blooms_levels: list[int] | None = None,
) -> list[PSOProposal]:
    """Propose a competency for each PSO. Strongest first, NONE omitted."""
    if not psos:
        return []
    weights = _idf_weights(psos)
    co_stems = set(_content_stems(co_statement))
    if not co_stems:
        return []

    out: list[PSOProposal] = []
    for p in psos:
        pso_stems = set(_content_stems(p.statement))
        shared = co_stems & pso_stems
        if not shared:
            continue
        # Normalised by the PSO's own weight mass, so a long PSO is not
        # automatically a better match than a short one.
        total = sum(weights.get(s, 0.0) for s in pso_stems) or 1.0
        score = sum(weights.get(s, 0.0) for s in shared) / total

        if score >= STRONG_THRESHOLD:
            comp = Competency.STRONG
        elif score >= PARTIAL_THRESHOLD:
            comp = Competency.PARTIAL
        else:
            continue

        # Same ceiling competency.py applies: a recall-level outcome cannot
        # claim a strong match on an outcome that asks the graduate to build
        # or investigate something.
        if comp is Competency.STRONG and blooms_levels:
            if action_level_for_bloom(blooms_levels) == 1 and p.action_level >= 2:
                comp = Competency.PARTIAL

        matched = sorted(shared - _GENERIC_PO_TERMS) or sorted(shared)
        out.append(PSOProposal(p.code, comp, round(score, 3), matched[:8]))

    out.sort(key=lambda x: (-x.score, x.pso_code))
    return out


@dataclass
class PSOMapping:
    co_code: str
    pso_code: str
    level: int
    competency: int          # 0 no match | 1 partial | 2 strong
    rationale: str
    evidence: str


def propose_pso_matrix(
    cos: dict[str, tuple[str, list[int]]],
    psos: list[PSO],
) -> list[PSOMapping]:
    """
    Full CO x PSO matrix, run through the same Rule 1 / Rule 2 engine the PO
    matrix uses -- so a PSO column is derived identically to a PO column and
    the two are directly comparable in Table 1.4.2.1.
    """
    by_code = {p.code: p for p in psos}
    out: list[PSOMapping] = []
    for co_code, (statement, blooms) in cos.items():
        for prop in propose_co_pso(statement, psos, blooms_levels=blooms):
            pso = by_code[prop.pso_code]
            res = map_co_to_po(
                po_code=prop.pso_code,
                competency=prop.competency,
                co_blooms_levels=blooms or [2],
                po_action_level=pso.action_level,
            )
            out.append(PSOMapping(
                co_code=co_code, pso_code=prop.pso_code, level=res.level,
                competency=int(prop.competency), rationale=res.rationale,
                evidence=prop.evidence,
            ))
    return out


def pso_levels_dict(mappings: list[PSOMapping]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for m in mappings:
        out.setdefault(m.co_code, {})[m.pso_code] = m.level
    return out
