"""
Measuring CO -> PO competency inference.

The mapping rules are exact and pinned by tests. Their *inputs* were not:
the competency lexicon had never been scored against anything, so "accurate"
was an assertion. This measures it the same way the question tagger is
measured, with the same dev/holdout discipline.

The metric that matters is **false strong** -- predicting competency 2 where
the gold says 0 or 1. A strong match runs Rule 2 and usually lands on level 3,
and a spurious 3 that gets bulk-approved is the failure the whole engine is
calibrated against. A miss costs a human one click; a false strong reaches a
SAR.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.engine.competency import propose_for_co
from app.engine.frameworks import get_framework

CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval" / "copo_corpus.json"


@dataclass(frozen=True)
class LabelledCO:
    course: str
    code: str
    statement: str
    blooms: list[int]
    gold: dict[str, int]


@dataclass
class CoPoEval:
    framework: str = "GAPC_V4"
    cells: int = 0                 # every CO x PO cell scored
    exact: int = 0                 # predicted competency == gold
    false_strong: list[dict] = field(default_factory=list)   # gold<2, predicted 2
    missed_strong: list[dict] = field(default_factory=list)  # gold 2, predicted <2
    missed_any: list[dict] = field(default_factory=list)     # gold>0, predicted 0
    spurious: list[dict] = field(default_factory=list)       # gold 0, predicted >0
    gold_strong: int = 0
    gold_any: int = 0
    predicted_strong: int = 0
    predicted_any: int = 0
    per_course: dict[str, dict] = field(default_factory=dict)

    @property
    def cell_accuracy(self) -> float:
        return self.exact / self.cells if self.cells else 0.0

    @property
    def false_strong_rate(self) -> float:
        """Share of predicted strongs that are not gold strongs."""
        return len(self.false_strong) / self.predicted_strong if self.predicted_strong else 0.0

    @property
    def strong_recall(self) -> float:
        return ((self.gold_strong - len(self.missed_strong)) / self.gold_strong
                if self.gold_strong else 0.0)

    @property
    def any_precision(self) -> float:
        """Of the cells we propose at all, how many are real mappings."""
        return ((self.predicted_any - len(self.spurious)) / self.predicted_any
                if self.predicted_any else 0.0)

    @property
    def any_recall(self) -> float:
        return ((self.gold_any - len(self.missed_any)) / self.gold_any
                if self.gold_any else 0.0)

    def summary(self) -> str:
        return "\n".join([
            f"Cells scored        {self.cells}",
            f"Cell accuracy       {self.cell_accuracy:.1%}",
            f"False-strong rate   {self.false_strong_rate:.1%}  "
            f"({len(self.false_strong)} of {self.predicted_strong} predicted strong)",
            f"Strong recall       {self.strong_recall:.1%}  "
            f"({self.gold_strong - len(self.missed_strong)}/{self.gold_strong})",
            f"Mapping precision   {self.any_precision:.1%}  "
            f"({len(self.spurious)} cells proposed that should be 0)",
            f"Mapping recall      {self.any_recall:.1%}  "
            f"({len(self.missed_any)} real mappings missed)",
        ])

    def to_dict(self) -> dict:
        return {
            "cells": self.cells,
            "cell_accuracy": round(self.cell_accuracy, 4),
            "false_strong_rate": round(self.false_strong_rate, 4),
            "strong_recall": round(self.strong_recall, 4),
            "any_precision": round(self.any_precision, 4),
            "any_recall": round(self.any_recall, 4),
            "false_strong": self.false_strong,
            "missed_strong": self.missed_strong,
            "spurious": self.spurious,
            "missed_any": self.missed_any,
            "per_course": self.per_course,
        }


def load_corpus(split: str | None = None,
                path: Path | str | None = None) -> tuple[str, list[LabelledCO]]:
    raw = json.loads(Path(path or CORPUS_PATH).read_text(encoding="utf-8"))
    out: list[LabelledCO] = []
    for c in raw["courses"]:
        if split and c.get("split", "dev") != split:
            continue
        for code, d in c["cos"].items():
            out.append(LabelledCO(c["code"], code, d["statement"],
                                  list(d["blooms"]), dict(d["gold"])))
    return raw.get("framework", "GAPC_V4"), out


def evaluate(split: str | None = None, *,
             corpus: list[LabelledCO] | None = None,
             framework: str = "GAPC_V4") -> CoPoEval:
    if corpus is None:
        framework, corpus = load_corpus(split)
    po_codes = list(get_framework(framework).po_codes)
    r = CoPoEval(framework=framework)
    per_course: dict[str, dict] = {}

    for co in corpus:
        predicted = {
            p.po_code: int(p.competency)
            for p in propose_for_co(co.statement, framework_code=framework,
                                    blooms_levels=co.blooms)
        }
        stats = per_course.setdefault(
            co.course, {"cells": 0, "exact": 0, "false_strong": 0, "missed": 0})

        for po in po_codes:
            g = co.gold.get(po, 0)
            p = predicted.get(po, 0)
            r.cells += 1
            stats["cells"] += 1
            if g == p:
                r.exact += 1
                stats["exact"] += 1
            if g == 2:
                r.gold_strong += 1
            if g > 0:
                r.gold_any += 1
            if p == 2:
                r.predicted_strong += 1
            if p > 0:
                r.predicted_any += 1

            item = {"course": co.course, "co": co.code, "po": po,
                    "gold": g, "predicted": p, "statement": co.statement[:90]}
            if p == 2 and g < 2:
                r.false_strong.append(item)
                stats["false_strong"] += 1
            if g == 2 and p < 2:
                r.missed_strong.append(item)
            if g > 0 and p == 0:
                r.missed_any.append(item)
                stats["missed"] += 1
            if g == 0 and p > 0:
                r.spurious.append(item)

    for k, v in per_course.items():
        v["accuracy"] = round(v["exact"] / v["cells"], 3) if v["cells"] else 0.0
    r.per_course = per_course
    return r


def main() -> None:                                      # pragma: no cover
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    show_dev = "--dev" in sys.argv
    for split in ("holdout", "dev"):
        if split == "dev" and not show_dev:
            continue
        r = evaluate(split)
        print(f"=== {split.upper()} ===")
        print(r.summary())
        if r.false_strong:
            print("\nFalse strongs (the dangerous ones):")
            for e in r.false_strong[:10]:
                print(f"  {e['course']} {e['co']} -> {e['po']} "
                      f"(gold {e['gold']}): {e['statement'][:66]}")
        if show_dev and r.missed_any:
            print("\nMissed mappings:")
            for e in r.missed_any[:10]:
                print(f"  {e['course']} {e['co']} -> {e['po']} (gold {e['gold']})")
        print()
    if not show_dev:
        print("(pass --dev to inspect the tuning split)")


if __name__ == "__main__":                               # pragma: no cover
    main()
