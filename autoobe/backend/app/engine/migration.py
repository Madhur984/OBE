"""
Migrating an existing 12-PO matrix to the 11-PO framework.

Every college that has been accredited already owns matrices under GAPC v3,
often hundreds of them, and re-authoring those by hand is the single largest
piece of manual work the framework change creates. It is also the piece most
likely to be done badly under time pressure, because eleven of the twelve
mappings look like a rename.

They are not a rename. Old PO6 (society) and old PO7 (environment and
sustainability) both fold into new PO6 (The Engineer and The World), so two
columns collapse into one, and everything below shifts up a slot. That has
three consequences this module handles explicitly:

1. **A collapsed cell needs a rule.** When a CO mapped to both old columns at
   different levels, the merged level is the *higher* of the two -- the
   outcome demonstrably reaches that level on one of the merged concerns, and
   averaging would understate a real strength. It is still a judgement, so
   every collapsed cell is flagged rather than applied silently.

2. **Column means move.** Two columns becoming one raises PO6's column mean
   and can push a matrix past the structural limits that were fine under v3.
   So the migrated matrix is re-validated, not assumed valid.

3. **Migration is not always the best answer.** The engine can also re-derive
   the mapping from the CO text under v4 directly. Neither is automatically
   right -- migration preserves the department's own judgement and its audit
   history, re-derivation may be better where the original mapping was weak --
   so both are produced and the disagreements listed. The department chooses,
   which is the same contract as everywhere else: propose, never impose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.competency import propose_matrix
from app.engine.constraints import validate_course_matrix
from app.engine.frameworks import get_framework, migrate_po_code
from app.engine.mapping_rules import Competency, map_co_to_po


@dataclass(frozen=True)
class CellDecision:
    co_code: str
    new_po: str
    level: int
    sources: tuple[str, ...]          # the v3 PO codes that fed this cell
    rule: str
    needs_review: bool

    @property
    def message(self) -> str:
        if len(self.sources) == 1:
            return (f"{self.co_code} → {self.new_po}: carried over from "
                    f"{self.sources[0]} at level {self.level}.")
        pairs = ", ".join(self.sources)
        return (f"{self.co_code} → {self.new_po}: {pairs} both merge into this "
                f"outcome. Kept level {self.level} ({self.rule}). Confirm the "
                f"merged cell still describes what the outcome actually does.")


@dataclass
class MigrationResult:
    levels: dict[str, dict[str, int]]           # CO -> {new PO -> level}
    decisions: list[CellDecision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def merged_cells(self) -> list[CellDecision]:
        return [d for d in self.decisions if len(d.sources) > 1]

    @property
    def review_count(self) -> int:
        return sum(1 for d in self.decisions if d.needs_review)


def migrate_matrix(
    levels_v3: dict[str, dict[str, int]],
    *,
    from_code: str = "GAPC_V3",
    to_code: str = "GAPC_V4",
) -> MigrationResult:
    """
    Mechanically migrate a CO x PO level matrix between frameworks.

    Preserves the department's own judgement wherever the mapping is 1:1,
    which is eleven of the twelve columns. Only the merge needs a rule.
    """
    result = MigrationResult(levels={})
    to_fw = get_framework(to_code)
    valid = set(to_fw.po_codes)

    for co_code, row in levels_v3.items():
        collected: dict[str, list[tuple[str, int]]] = {}
        for old_po, level in row.items():
            if level <= 0:
                continue
            try:
                targets = migrate_po_code(old_po, from_code=from_code, to_code=to_code)
            except (KeyError, ValueError):
                result.warnings.append(
                    f"{co_code}: {old_po} has no equivalent in {to_fw.name} and "
                    f"was dropped. Check it was not a locally invented column."
                )
                continue
            for new_po in targets:
                if new_po not in valid:
                    continue
                collected.setdefault(new_po, []).append((old_po, level))

        new_row: dict[str, int] = {}
        for new_po, pairs in collected.items():
            if len(pairs) == 1:
                old_po, level = pairs[0]
                new_row[new_po] = level
                result.decisions.append(CellDecision(
                    co_code, new_po, level, (old_po,), "direct carry-over", False,
                ))
            else:
                level = max(l for _, l in pairs)
                sources = tuple(sorted(p for p, _ in pairs))
                differing = len({l for _, l in pairs}) > 1
                new_row[new_po] = level
                result.decisions.append(CellDecision(
                    co_code, new_po, level, sources,
                    "higher of the merged levels", differing,
                ))
        result.levels[co_code] = new_row

    merged = len(result.merged_cells)
    if merged:
        result.warnings.append(
            f"{merged} cell(s) came from the PO6+PO7 merge. Two columns became "
            f"one, so PO6's column mean rises — the structural checks below "
            f"were re-run on the migrated matrix, not assumed from the original."
        )
    return result


@dataclass(frozen=True)
class Disagreement:
    co_code: str
    po_code: str
    migrated: int
    rederived: int

    @property
    def message(self) -> str:
        if self.migrated == 0:
            return (f"{self.co_code} × {self.po_code}: the outcome text supports "
                    f"level {self.rederived}, but the legacy matrix had no mapping "
                    f"here. Possibly a gap the original missed.")
        if self.rederived == 0:
            return (f"{self.co_code} × {self.po_code}: the legacy matrix claims "
                    f"level {self.migrated}, but the outcome text supports no "
                    f"mapping. Either the wording understates the outcome or the "
                    f"original cell was optimistic.")
        return (f"{self.co_code} × {self.po_code}: legacy says {self.migrated}, "
                f"the outcome text supports {self.rederived}.")


@dataclass
class ComparisonResult:
    migrated: MigrationResult
    rederived: dict[str, dict[str, int]]
    disagreements: list[Disagreement] = field(default_factory=list)
    validation_migrated: dict = field(default_factory=dict)
    validation_rederived: dict = field(default_factory=dict)

    @property
    def agreement_rate(self) -> float:
        cells = set()
        for row in (list(self.migrated.levels.items()) + list(self.rederived.items())):
            co, mapping = row
            for po in mapping:
                cells.add((co, po))
        if not cells:
            return 1.0
        return round(1 - len(self.disagreements) / len(cells), 3)

    @property
    def recommendation(self) -> str:
        """
        Which to adopt. Migration is the default because it preserves the
        department's own judgement and the audit trail that goes with it;
        re-derivation only wins when the legacy matrix looks structurally
        unsound, in which case the framework change is a good moment to fix it.
        """
        mig_errors = len([f for f in self.validation_migrated.get("findings", [])
                          if f.get("severity") == "error"])
        red_errors = len([f for f in self.validation_rederived.get("findings", [])
                          if f.get("severity") == "error"])
        if mig_errors and red_errors < mig_errors:
            return (
                f"Re-derive. The migrated matrix carries {mig_errors} structural "
                f"error(s) forward, against {red_errors} for a fresh derivation — "
                f"the legacy matrix was already over-mapped, and porting it "
                f"ports the problem."
            )
        if self.agreement_rate >= 0.85:
            return (
                "Migrate. The legacy matrix and the outcome text agree on "
                f"{self.agreement_rate:.0%} of cells, so migration preserves the "
                f"department's judgement without introducing anything new."
            )
        return (
            f"Migrate, then review the {len(self.disagreements)} disagreement(s). "
            f"Agreement is only {self.agreement_rate:.0%}, which usually means the "
            f"CO statements were rewritten since the matrix was authored — the "
            f"cells below are where the two have drifted apart."
        )


def migrate_and_compare(
    levels_v3: dict[str, dict[str, int]],
    cos: dict[str, tuple[str, list[int]]],
    *,
    from_code: str = "GAPC_V3",
    to_code: str = "GAPC_V4",
    po_action_levels: dict[str, int] | None = None,
) -> ComparisonResult:
    """
    Produce both the migrated matrix and one freshly derived from the CO
    text under the new framework, and list where they differ.
    """
    migrated = migrate_matrix(levels_v3, from_code=from_code, to_code=to_code)

    to_fw = get_framework(to_code)
    action_levels = po_action_levels or dict(to_fw.po_action_levels)

    competency = propose_matrix(cos, framework_code=to_code)
    rederived: dict[str, dict[str, int]] = {}
    for co_code, per_po in competency.items():
        blooms = cos.get(co_code, ("", [2]))[1] or [2]
        row: dict[str, int] = {}
        for po_code, comp in per_po.items():
            res = map_co_to_po(
                po_code=po_code, competency=Competency(comp),
                co_blooms_levels=blooms,
                po_action_level=action_levels.get(po_code, 2),
            )
            if res.level > 0:
                row[po_code] = res.level
        rederived[co_code] = row

    disagreements: list[Disagreement] = []
    for co_code in sorted(set(migrated.levels) | set(rederived)):
        m_row = migrated.levels.get(co_code, {})
        r_row = rederived.get(co_code, {})
        for po in sorted(set(m_row) | set(r_row)):
            m, r = m_row.get(po, 0), r_row.get(po, 0)
            if m != r:
                disagreements.append(Disagreement(co_code, po, m, r))

    return ComparisonResult(
        migrated=migrated,
        rederived=rederived,
        disagreements=disagreements,
        validation_migrated=_validate(migrated.levels, to_code),
        validation_rederived=_validate(rederived, to_code),
    )


def _validate(levels: dict[str, dict[str, int]], framework_code: str) -> dict:
    report = validate_course_matrix(levels, framework_code=framework_code)
    return {
        "is_valid": report.is_valid,
        "findings": [
            {"severity": v.severity.value, "message": v.message,
             "check": v.rule, "cells": v.cells}
            for v in report.violations
        ],
    }
