"""
Deterministic CO -> PO mapping rule engine (Layer 1).

Implements the two-rule algorithm from "Basic guidelines for framing COs":

  Rule 1 - Competency Matching
    Competency 0 (no match)      -> mapping level 0 (rendered "-" in the SAR)
    Competency 1 (partial match) -> mapping level 1
    Competency 2 (strong match)  -> defer to Rule 2

  Rule 2 - Action Level (strong matches only)
    CL = CO action level, from Bloom's (K1-2 -> 1, K3-4 -> 2, K5-6 -> 3)
    PL = PO action level
    If CL >= PL -> mapping level 3
    If CL <  PL -> mapping level = CL

Note the second branch: it assigns **CL**, the outcome's own action level,
not PL. The distinction matters and is easy to get backwards. A K2 outcome
("Explain...") against a PL3 design PO cannot be credited at 3 just because
the PO is demanding -- it maps at 1, because 1 is what the outcome actually
demonstrates. Assigning PL would let a weak outcome inherit the strength of
whatever it is mapped against, which inflates exactly the cells a peer team
looks at hardest.

Competency is 0/1/2 rather than none/partial/strong: the guidelines and the
matrices faculty already fill in are numeric, and a scale that reads 0-1-2 in
the tool and none-partial-strong in the document is a translation step that
buys nothing. The names remain available as aliases.

No I/O, no network, no database. Pure functions only, so the whole thing is
reproducible and unit-testable -- the property AutoOBE_v3 Section 9 requires
of the mapping engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from app.engine.blooms import action_level_for_bloom


class Competency(IntEnum):
    """
    How well the outcome's competency matches the PO's.

    An IntEnum so `Competency.STRONG == 2` and the numeric form round-trips
    through JSON, spreadsheets, and the UI without a lookup table.
    """
    NONE = 0
    PARTIAL = 1
    STRONG = 2

    @property
    def label(self) -> str:
        return {0: "no match", 1: "partial", 2: "strong"}[int(self)]


# Accepted spellings on the way in. The engine emits integers; it accepts the
# older strings so stored matrices and third-party payloads keep working.
_ALIASES: dict[str, Competency] = {
    "0": Competency.NONE, "none": Competency.NONE, "no": Competency.NONE,
    "no match": Competency.NONE, "-": Competency.NONE, "": Competency.NONE,
    "1": Competency.PARTIAL, "partial": Competency.PARTIAL,
    "partial match": Competency.PARTIAL, "moderate": Competency.PARTIAL,
    "2": Competency.STRONG, "strong": Competency.STRONG,
    "strong match": Competency.STRONG, "full": Competency.STRONG,
}


def parse_competency(value: object) -> Competency:
    """Coerce 0/1/2, '0'/'1'/'2', or a legacy name into a Competency."""
    if isinstance(value, Competency):
        return value
    if isinstance(value, bool):
        # bool is an int subclass, so True would otherwise slip through as 1.
        raise ValueError(
            f"Competency must be 0 (no match), 1 (partial) or 2 (strong); "
            f"got {value!r}"
        )
    if isinstance(value, int):
        try:
            return Competency(value)
        except ValueError:
            raise ValueError(
                f"Competency must be 0 (no match), 1 (partial) or 2 (strong); "
                f"got {value!r}"
            ) from None
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _ALIASES:
            return _ALIASES[key]
    raise ValueError(
        f"Competency must be 0 (no match), 1 (partial) or 2 (strong); got {value!r}"
    )


NO_CORRELATION = 0  # exported to the SAR as "-", never as 0


@dataclass(frozen=True)
class MappingResult:
    po_code: str
    level: int                 # 0-3; 0 means no correlation
    rule_applied: str
    co_action_level: int | None
    po_action_level: int
    rationale: str
    competency: int = 0

    @property
    def renders_as(self) -> str:
        return render_level(self.level)


def map_co_to_po(
    *,
    po_code: str,
    competency: Competency | int | str,
    co_blooms_levels: list[int],
    po_action_level: int,
) -> MappingResult:
    """Apply Rule 1, then Rule 2 where Rule 1 defers."""
    if not (1 <= po_action_level <= 3):
        raise ValueError(f"PO action level must be 1-3, got {po_action_level!r}")
    comp = parse_competency(competency)

    if comp is Competency.NONE:
        return MappingResult(
            po_code=po_code, level=NO_CORRELATION, rule_applied="Rule 1 (competency 0)",
            co_action_level=None, po_action_level=po_action_level,
            competency=int(comp),
            rationale="Rule 1 — competency 0: no match between the course outcome and this PO.",
        )

    if comp is Competency.PARTIAL:
        return MappingResult(
            po_code=po_code, level=1, rule_applied="Rule 1 (competency 1)",
            co_action_level=None, po_action_level=po_action_level,
            competency=int(comp),
            rationale="Rule 1 — competency 1 (partial match) fixes the mapping level at 1.",
        )

    cl = action_level_for_bloom(co_blooms_levels)
    if cl >= po_action_level:
        level = 3
        why = f"CL {cl} >= PL {po_action_level}, so the mapping level is 3"
    else:
        # CL, not PL. The outcome is credited with what it demonstrates.
        level = cl
        why = (f"CL {cl} < PL {po_action_level}, so the mapping level is the "
               f"outcome's own action level, {cl}")

    return MappingResult(
        po_code=po_code, level=level, rule_applied="Rule 2 (action level)",
        co_action_level=cl, po_action_level=po_action_level, competency=int(comp),
        rationale=(
            f"Rule 2 — competency 2 (strong match); {why}. "
            f"CL derived from Bloom's level(s) {sorted(co_blooms_levels)}."
        ),
    )


def render_level(level: int) -> str:
    """SAR articulation matrices print '-' for no correlation, never 0."""
    return "-" if level == NO_CORRELATION else str(level)
