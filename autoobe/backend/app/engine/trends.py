"""
Longitudinal attainment: trends, gaps, and CQI closure evidence.

Criterion 8 is not satisfied by a table of this year's numbers. It asks
whether the department noticed a weakness, did something about it, and can
show the result -- a loop, over years. That needs stored history, which is
what `store.attainment_runs` now provides, and it needs one thing that is
harder than it looks: a trend line that survives the framework change.

**The framework boundary.** A department accredited under GAPC v3 has years
of PO8 (Ethics) data; under v4 that outcome is PO7. Plotting "PO7" across the
boundary silently splices Ethics onto Environment-and-Sustainability and
produces a trend line that is not about anything. Worse, old PO6 *and* old
PO7 both fold into new PO6, so one point in the new series corresponds to two
in the old. This module aligns series through the published crosswalk,
averages the merged pair, and marks every point that came from a merge -- so
the chart is honest about which years are directly comparable.

**Gaps, not just values.** A PO at 2.1 against a target of 2.0 and a PO at
2.1 against a target of 2.5 are different situations, and only the second is
a Criterion 8 action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from app.engine.frameworks import get_framework
from app.seed.gapc_v4_po import CROSSWALK_V3_TO_V4


@dataclass(frozen=True)
class Point:
    academic_year: str
    value: float | None
    framework_code: str
    merged_from: tuple[str, ...] = ()

    @property
    def is_merged(self) -> bool:
        return len(self.merged_from) > 1


@dataclass
class Series:
    """One outcome's attainment over time, expressed in one framework."""
    target_code: str
    framework_code: str
    points: list[Point] = field(default_factory=list)
    target: float | None = None

    @property
    def values(self) -> list[float]:
        return [p.value for p in self.points if p.value is not None]

    @property
    def latest(self) -> float | None:
        for p in reversed(self.points):
            if p.value is not None:
                return p.value
        return None

    @property
    def first(self) -> float | None:
        for p in self.points:
            if p.value is not None:
                return p.value
        return None

    @property
    def change(self) -> float | None:
        a, b = self.first, self.latest
        if a is None or b is None or len(self.values) < 2:
            return None
        return round(b - a, 3)

    @property
    def direction(self) -> str:
        c = self.change
        if c is None:
            return "insufficient-data"
        if c >= 0.15:
            return "improving"
        if c <= -0.15:
            return "declining"
        return "flat"

    @property
    def gap(self) -> float | None:
        if self.target is None or self.latest is None:
            return None
        return round(self.latest - self.target, 3)

    @property
    def meets_target(self) -> bool | None:
        g = self.gap
        return None if g is None else g >= 0

    @property
    def has_merged_points(self) -> bool:
        return any(p.is_merged for p in self.points)

    def to_dict(self) -> dict:
        return {
            "target_code": self.target_code,
            "framework_code": self.framework_code,
            "target": self.target,
            "latest": self.latest,
            "change": self.change,
            "direction": self.direction,
            "gap": self.gap,
            "meets_target": self.meets_target,
            "has_merged_points": self.has_merged_points,
            "points": [
                {"academic_year": p.academic_year, "value": p.value,
                 "framework_code": p.framework_code,
                 "merged_from": list(p.merged_from)}
                for p in self.points
            ],
        }


def _to_v4(po_values: dict[str, float | None]) -> dict[str, tuple[float | None, tuple[str, ...]]]:
    """
    Express a v3 result in v4 codes. Old PO6 and PO7 both land on new PO6, so
    the pair is averaged and both sources recorded -- an average is the only
    defensible collapse, and hiding that it happened is not an option.
    """
    gathered: dict[str, list[tuple[str, float]]] = {}
    for old_code, value in po_values.items():
        for new_code in CROSSWALK_V3_TO_V4.get(old_code, ()):
            if value is not None:
                gathered.setdefault(new_code, []).append((old_code, value))
            else:
                gathered.setdefault(new_code, [])
    out: dict[str, tuple[float | None, tuple[str, ...]]] = {}
    for new_code, pairs in gathered.items():
        if not pairs:
            out[new_code] = (None, ())
        else:
            out[new_code] = (round(mean(v for _, v in pairs), 3),
                             tuple(sorted(c for c, _ in pairs)))
    return out


def build_series(
    runs: list[dict],
    *,
    framework_code: str = "GAPC_V4",
    targets: dict[str, float] | None = None,
    key: str = "po_direct_attainment",
) -> list[Series]:
    """
    Turn stored program-level runs into one Series per outcome.

    `runs` are rows from `TenantStore.list_runs`, each carrying a
    `framework_code` in its config and a `{po_code: value}` map under `key`
    in its result. Runs from either framework are accepted and aligned to
    `framework_code`.
    """
    fw = get_framework(framework_code)
    by_year: dict[str, dict[str, tuple[float | None, tuple[str, ...]]]] = {}
    year_framework: dict[str, str] = {}

    for run in sorted(runs, key=lambda r: (r.get("academic_year", ""),
                                           r.get("created_at", ""))):
        year = run.get("academic_year", "")
        values = (run.get("result") or {}).get(key) or {}
        src_fw = (run.get("config") or {}).get("framework_code", framework_code)
        year_framework[year] = src_fw

        if src_fw == framework_code:
            by_year[year] = {k: (v, ()) for k, v in values.items()}
        elif src_fw == "GAPC_V3" and framework_code == "GAPC_V4":
            by_year[year] = _to_v4(values)
        else:
            # No published crosswalk in this direction: v4 -> v3 cannot be
            # undone, because one merged PO6 cannot be split back into two.
            by_year[year] = {}

    years = sorted(by_year)
    series: list[Series] = []
    for code in fw.po_codes:
        pts = []
        for y in years:
            value, merged = by_year[y].get(code, (None, ()))
            pts.append(Point(y, value, year_framework.get(y, framework_code), merged))
        series.append(Series(code, framework_code, pts,
                             (targets or {}).get(code)))
    return series


@dataclass(frozen=True)
class GapFinding:
    target_code: str
    severity: str          # error | warning | info
    message: str


def gap_analysis(series: list[Series]) -> list[GapFinding]:
    """
    The findings a Criterion 8 narrative is written from. Ordered so the
    department reads what it must act on first.
    """
    out: list[GapFinding] = []
    for s in series:
        if s.latest is None:
            out.append(GapFinding(
                s.target_code, "error",
                f"{s.target_code} has no attainment in any recorded year. An "
                f"outcome no course evidences is an automatic finding.",
            ))
            continue

        if s.meets_target is False:
            trend = (" and is still declining" if s.direction == "declining"
                     else " and has not moved" if s.direction == "flat"
                     else ", though it is improving")
            out.append(GapFinding(
                s.target_code, "error" if s.direction != "improving" else "warning",
                f"{s.target_code} is at {s.latest:.2f} against a target of "
                f"{s.target:.2f} — short by {abs(s.gap):.2f}{trend}. "
                f"Criterion 8 needs a stated action for this.",
            ))
        elif s.direction == "declining":
            out.append(GapFinding(
                s.target_code, "warning",
                f"{s.target_code} still meets its target but has fallen "
                f"{abs(s.change):.2f} since {s.points[0].academic_year}. "
                f"A declining outcome that is still above target is the one "
                f"most often missed until it is below.",
            ))
        elif s.direction == "improving":
            out.append(GapFinding(
                s.target_code, "info",
                f"{s.target_code} improved by {s.change:.2f} to {s.latest:.2f}. "
                f"If an action was recorded for it, this is the closure "
                f"evidence Criterion 8 asks for.",
            ))

        if s.has_merged_points:
            merged_years = [p.academic_year for p in s.points if p.is_merged]
            out.append(GapFinding(
                s.target_code, "info",
                f"{s.target_code}: {', '.join(merged_years)} predate the "
                f"framework change and are the average of two legacy outcomes. "
                f"Comparable in direction, not point for point — say so in the "
                f"narrative rather than letting the chart imply otherwise.",
            ))

    rank = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (rank[f.severity], f.target_code))
    return out


@dataclass
class ClosureCheck:
    """Did a recorded action actually move the outcome it targeted?"""
    target_code: str
    action: str
    action_year: str
    before: float | None
    after: float | None
    delta: float | None
    verdict: str           # closed | no-effect | worsened | pending
    message: str


def check_closure(
    series: list[Series],
    actions: list[dict],
) -> list[ClosureCheck]:
    """
    Match recorded CQI actions to what happened next.

    `actions` are dicts with `target_code`, `action`, and `academic_year` --
    the year the action was *taken*. The comparison is that year's attainment
    against the following year's, because an action taken during a year shows
    up in the next cohort.

    "no-effect" is reported as plainly as "closed". A CQI loop that records
    only its successes is the exact pattern peer teams are trained to look for.
    """
    by_code = {s.target_code: s for s in series}
    out: list[ClosureCheck] = []

    for a in actions:
        code = a.get("target_code", "")
        s = by_code.get(code)
        year = a.get("academic_year", "")
        action = a.get("action", "")
        if s is None:
            out.append(ClosureCheck(code, action, year, None, None, None, "pending",
                                    f"No attainment series for {code}."))
            continue

        years = [p.academic_year for p in s.points]
        if year not in years:
            out.append(ClosureCheck(code, action, year, None, None, None, "pending",
                                    f"No attainment recorded for {year}."))
            continue
        idx = years.index(year)
        before = s.points[idx].value
        after = s.points[idx + 1].value if idx + 1 < len(s.points) else None

        if after is None:
            out.append(ClosureCheck(
                code, action, year, before, None, None, "pending",
                f"{code}: action taken in {year}; the following year's attainment "
                f"is not yet recorded, so the loop is open.",
            ))
            continue

        delta = round(after - before, 3) if before is not None else None
        if delta is None:
            verdict, msg = "pending", f"{code}: no baseline for {year}."
        elif delta >= 0.15:
            verdict = "closed"
            msg = (f"{code}: rose from {before:.2f} to {after:.2f} "
                   f"(+{delta:.2f}) the year after “{action}”. This is closure "
                   f"evidence — cite both figures.")
        elif delta <= -0.15:
            verdict = "worsened"
            msg = (f"{code}: fell from {before:.2f} to {after:.2f} ({delta:.2f}) "
                   f"despite “{action}”. Record why, and what replaced it — a "
                   f"documented failed action is stronger evidence of a working "
                   f"loop than silence.")
        else:
            verdict = "no-effect"
            msg = (f"{code}: essentially unchanged ({before:.2f} → {after:.2f}) "
                   f"after “{action}”. The loop ran but the action did not move "
                   f"the outcome; a further action is needed.")
        out.append(ClosureCheck(code, action, year, before, after, delta, verdict, msg))

    order = {"worsened": 0, "no-effect": 1, "pending": 2, "closed": 3}
    out.sort(key=lambda c: (order[c.verdict], c.target_code))
    return out


def summarise(series: list[Series]) -> dict:
    """Headline numbers for the trend panel."""
    with_data = [s for s in series if s.latest is not None]
    with_target = [s for s in with_data if s.meets_target is not None]
    return {
        "outcomes": len(series),
        "with_data": len(with_data),
        "years": [p.academic_year for p in (series[0].points if series else [])],
        "meeting_target": sum(1 for s in with_target if s.meets_target),
        "below_target": sum(1 for s in with_target if not s.meets_target),
        "improving": sum(1 for s in with_data if s.direction == "improving"),
        "declining": sum(1 for s in with_data if s.direction == "declining"),
        "flat": sum(1 for s in with_data if s.direction == "flat"),
        "crosses_framework_boundary": any(s.has_merged_points for s in series),
    }
