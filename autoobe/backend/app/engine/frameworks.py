"""
Framework registry -- the dual-framework requirement.

AutoOBE_Implementation_Plan.pdf Section 3 is explicit: GAPC v3 (12 POs) and
GAPC v4.0 (11 POs) are BOTH first-class and permanent. A college has
graduated cohorts mapped under v3 and current cohorts under v4, often in the
same academic year, and Criterion 8 needs trend lines across that boundary.

The hard rule this module exists to enforce: nothing anywhere in the codebase
may hardcode a PO count or assume PO ordinals are stable across versions.
Everything resolves through (framework_version, po_code).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.seed import gapc_v3_po, gapc_v4_po


@dataclass(frozen=True)
class Framework:
    code: str
    name: str
    po_codes: tuple[str, ...]
    po_titles: dict[str, str]
    po_statements: dict[str, str]
    po_action_levels: dict[str, int]
    po_wk_refs: dict[str, tuple[str, ...]]
    wk_statements: dict[str, str]

    @property
    def po_count(self) -> int:
        return len(self.po_codes)


def _build_v3() -> Framework:
    pos = gapc_v3_po.PROGRAM_OUTCOMES
    return Framework(
        code=gapc_v3_po.FRAMEWORK_CODE,
        name=gapc_v3_po.FRAMEWORK_NAME,
        po_codes=tuple(p.po_code for p in pos),
        po_titles={p.po_code: p.title for p in pos},
        po_statements={p.po_code: p.statement for p in pos},
        po_action_levels={p.po_code: p.default_action_level for p in pos},
        po_wk_refs={p.po_code: () for p in pos},  # WK profile is a v4.0 concept
        wk_statements={},
    )


def _build_v4() -> Framework:
    pos = gapc_v4_po.PROGRAM_OUTCOMES
    return Framework(
        code=gapc_v4_po.FRAMEWORK_CODE,
        name=gapc_v4_po.FRAMEWORK_NAME,
        po_codes=tuple(p.po_code for p in pos),
        po_titles={p.po_code: p.title for p in pos},
        po_statements={p.po_code: p.statement for p in pos},
        po_action_levels={p.po_code: p.default_action_level for p in pos},
        po_wk_refs={p.po_code: p.wk_refs for p in pos},
        wk_statements={w.wk_code: w.statement for w in gapc_v4_po.KNOWLEDGE_PROFILE},
    )


FRAMEWORKS: dict[str, Framework] = {
    gapc_v3_po.FRAMEWORK_CODE: _build_v3(),
    gapc_v4_po.FRAMEWORK_CODE: _build_v4(),
}

DEFAULT_FRAMEWORK = gapc_v4_po.FRAMEWORK_CODE
# What the "/" page opens on, which is not the same decision as the engine
# default above. Departments are still filing SARs against the 12-PO v3
# framework -- the mapping sheet this tool has to reproduce is PO1..PO12 plus
# PSO1/PSO2 -- while the engine default and the evaluation corpus stay on v4.
# Changing DEFAULT_FRAMEWORK instead would relabel the eval corpus.
UI_DEFAULT_FRAMEWORK = gapc_v3_po.FRAMEWORK_CODE


def lifelong_po_code(framework_code: str) -> str | None:
    """
    The code of the life-long learning PO, or None if the framework has none.

    Resolved by title rather than by ordinal, because it is PO12 in GAPC v3
    and PO11 in v4, and the two seeds do not even spell it the same way
    ("Life-long Learning" against "Life-Long Learning").
    """
    fw = get_framework(framework_code)
    for code in fw.po_codes:
        squashed = fw.po_titles[code].lower().replace("-", "").replace(" ", "")
        if squashed == "lifelonglearning":
            return code
    return None


def get_framework(code: str) -> Framework:
    try:
        return FRAMEWORKS[code]
    except KeyError:
        raise ValueError(
            f"Unknown framework {code!r}. Known: {sorted(FRAMEWORKS)}"
        ) from None


def migrate_po_code(po_code: str, *, from_code: str, to_code: str) -> tuple[str, ...]:
    """
    Translate a PO code across framework versions using the Section 3.4
    crosswalk. Returns a tuple because the mapping is not 1:1: old PO6 and
    old PO7 BOTH land on new PO6.

    Callers migrating attainment data must keep both source values and
    document the blend -- never silently drop one (Section 3.4).
    """
    if from_code == to_code:
        return (po_code,)
    if from_code == "GAPC_V3" and to_code == "GAPC_V4":
        try:
            return gapc_v4_po.CROSSWALK_V3_TO_V4[po_code]
        except KeyError:
            raise ValueError(f"{po_code!r} is not a valid GAPC v3 PO code") from None
    if from_code == "GAPC_V4" and to_code == "GAPC_V3":
        sources = tuple(
            v3 for v3, v4s in gapc_v4_po.CROSSWALK_V3_TO_V4.items() if po_code in v4s
        )
        if not sources:
            raise ValueError(f"{po_code!r} is not a valid GAPC v4.0 PO code")
        return sources
    raise ValueError(f"No crosswalk defined from {from_code!r} to {to_code!r}")
