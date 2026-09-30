"""
Layer 6 -- the structural constraint solver.

Auto-generated matrices over-map. These checks run before anything persists,
because a prompt can be ignored but an assertion cannot. Every violation names
the offending cells so the UI can point at them directly.

Two audiences:
  * course-level checks catch a single implausible matrix
  * program-level checks catch orphan POs and PSOs, which are automatic NBA
    findings and feed SAR Table 1.3.2 (PO -> list of courses) directly
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MAX_POS_PER_CO = 6
MIN_POS_PER_CO = 1
MAX_LEVEL3_PER_CO = 2
MAX_COLUMN_MEAN = 2.6
# POs that written technical coursework rarely evidences well. A Data
# Structures course mapping every CO to "The Engineer and the World" is the
# single most common peer-team objection.
SPARSE_POS = {
    "GAPC_V3": ("PO6", "PO7", "PO8", "PO11"),
    "GAPC_V4": ("PO6", "PO7", "PO10"),
}
SPARSE_MAX_FRACTION = 0.5


class Severity(str, Enum):
    ERROR = "error"      # must be fixed before the matrix is usable
    WARNING = "warning"  # defensible, but a peer team will ask about it


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: Severity
    message: str
    cells: list[str]


@dataclass(frozen=True)
class ValidationReport:
    violations: list[Violation]

    @property
    def is_valid(self) -> bool:
        return not any(v.severity is Severity.ERROR for v in self.violations)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity is Severity.WARNING]


def validate_course_matrix(
    co_po_levels: dict[str, dict[str, int]],
    *,
    framework_code: str,
    home_pos: tuple[str, ...] = (),
) -> ValidationReport:
    """
    Validate one course's articulation matrix.

    `home_pos` exempts a course from the column-mean cap for POs it genuinely
    exists to deliver -- a Professional Ethics course should map heavily to
    the ethics PO, and flagging that would be noise.
    """
    v: list[Violation] = []
    sparse = SPARSE_POS.get(framework_code, ())

    for co, levels in sorted(co_po_levels.items()):
        mapped = {po: lv for po, lv in levels.items() if lv > 0}

        if len(mapped) > MAX_POS_PER_CO:
            v.append(Violation(
                "max_pos_per_co", Severity.ERROR,
                f"{co} maps to {len(mapped)} POs; at most {MAX_POS_PER_CO} is credible "
                f"for a single outcome. Keep the strongest and drop the rest.",
                [f"{co}:{po}" for po in sorted(mapped)],
            ))
        elif len(mapped) < MIN_POS_PER_CO:
            v.append(Violation(
                "orphan_co", Severity.ERROR,
                f"{co} maps to no PO at all. Every course outcome must address "
                f"at least one program outcome or it cannot contribute to attainment.",
                [co],
            ))

        threes = [po for po, lv in mapped.items() if lv == 3]
        if len(threes) > MAX_LEVEL3_PER_CO:
            v.append(Violation(
                "max_level3_per_co", Severity.WARNING,
                f"{co} claims substantial (3) correlation with {len(threes)} POs. "
                f"At most {MAX_LEVEL3_PER_CO} is usually defensible; the rest are "
                f"likely moderate.",
                [f"{co}:{po}" for po in sorted(threes)],
            ))

    if co_po_levels:
        all_pos = {po for levels in co_po_levels.values() for po in levels}
        for po in sorted(all_pos):
            if po in home_pos:
                continue
            vals = [levels.get(po, 0) for levels in co_po_levels.values()]
            mapped_vals = [x for x in vals if x > 0]
            if not mapped_vals:
                continue

            mean = sum(mapped_vals) / len(mapped_vals)
            if mean > MAX_COLUMN_MEAN:
                v.append(Violation(
                    "column_mean", Severity.WARNING,
                    f"Average correlation with {po} is {mean:.2f} across the COs that "
                    f"address it, above the {MAX_COLUMN_MEAN} guideline. Unless this "
                    f"course is a primary vehicle for {po}, some of these are overstated.",
                    [f"{co}:{po}" for co, lv in co_po_levels.items() if lv.get(po, 0) > 0],
                ))

            if po in sparse:
                frac = len(mapped_vals) / len(co_po_levels)
                if frac > SPARSE_MAX_FRACTION:
                    v.append(Violation(
                        "sparse_po", Severity.WARNING,
                        f"{len(mapped_vals)} of {len(co_po_levels)} COs map to {po}. "
                        f"Written technical coursework rarely evidences this PO well -- "
                        f"rubric-assessed projects, internships or seminars usually do. "
                        f"Expect a peer team to ask what evidence supports it.",
                        [f"{co}:{po}" for co, lv in co_po_levels.items() if lv.get(po, 0) > 0],
                    ))
    return ValidationReport(v)


def validate_program_matrix(
    course_matrices: dict[str, dict[str, dict[str, int]]],
    *,
    framework_code: str,
    po_codes: list[str],
    pso_codes: list[str] | None = None,
    min_courses_per_po: int = 2,
) -> ValidationReport:
    """
    Program-wide checks. An orphan PO -- one no course addresses at level >= 2
    -- is an automatic NBA finding, so it is an error here, not a warning.
    """
    v: list[Violation] = []
    coverage: dict[str, list[str]] = {po: [] for po in po_codes}

    for course, matrix in course_matrices.items():
        for levels in matrix.values():
            for po, lv in levels.items():
                if lv >= 2 and po in coverage and course not in coverage[po]:
                    coverage[po].append(course)

    for po in po_codes:
        n = len(coverage[po])
        if n == 0:
            v.append(Violation(
                "orphan_po", Severity.ERROR,
                f"No course addresses {po} at moderate correlation or above. "
                f"An unaddressed program outcome is an automatic finding -- either "
                f"a course is under-mapped, or the curriculum has a real gap to "
                f"report under SAR 1.2.3.",
                [po],
            ))
        elif n < min_courses_per_po:
            v.append(Violation(
                "thin_po_coverage", Severity.WARNING,
                f"Only {n} course addresses {po} at level 2 or above. Attainment "
                f"resting on a single course is fragile and hard to defend.",
                [po],
            ))

    for pso in (pso_codes or []):
        covered = any(
            lv >= 2
            for matrix in course_matrices.values()
            for levels in matrix.values()
            for po, lv in levels.items() if po == pso
        )
        if not covered:
            v.append(Violation(
                "orphan_pso", Severity.ERROR,
                f"No course addresses {pso} at level 2 or above. A program specific "
                f"outcome the curriculum does not deliver cannot be claimed.",
                [pso],
            ))
    return ValidationReport(v)


def po_course_coverage(
    course_matrices: dict[str, dict[str, dict[str, int]]],
    po_codes: list[str],
    *,
    min_level: int = 1,
) -> dict[str, list[str]]:
    """SAR Table 1.3.2: PO -> the courses that address it."""
    out: dict[str, list[str]] = {po: [] for po in po_codes}
    for course, matrix in sorted(course_matrices.items()):
        for levels in matrix.values():
            for po, lv in levels.items():
                if lv >= min_level and po in out and course not in out[po]:
                    out[po].append(course)
    return out
