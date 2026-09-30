"""
SAR table builders.

Output shapes match the official NBA SAR templates exactly -- built against
the real table structure, not an approximation:

  Table 1.4.2.1  Course Articulation Matrix   (CO rows x PO/PSO columns)
  Table 1.5.1    Program Articulation Matrix  (course rows x PO/PSO columns)
  Table 3.8.1    Direct PO/PSO attainment     (course rows + average row)

Column count follows the framework, never a constant -- the same builders
emit 12 columns for GAPC v3 and 11 for GAPC v4.0.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.frameworks import get_framework
from app.engine.mapping_rules import render_level


@dataclass(frozen=True)
class Table:
    number: str
    title: str
    headers: list[str]
    rows: list[list[str]]
    # Indices into `rows` that are totals or averages rather than data. The
    # renderers bolded these by sniffing the first cell for "average"; a
    # course block repeats the course title on its average row instead, so
    # the row has to say what it is rather than be guessed at.
    summary_rows: tuple[int, ...] = ()

    def to_markdown(self) -> str:
        sep = ["---"] * len(self.headers)
        lines = [
            f"**Table No. {self.number}:** {self.title}",
            "",
            "| " + " | ".join(self.headers) + " |",
            "| " + " | ".join(sep) + " |",
        ]
        lines.extend("| " + " | ".join(r) + " |" for r in self.rows)
        return "\n".join(lines)

    def to_csv(self) -> str:
        def esc(v: str) -> str:
            return f'"{v}"' if ("," in v or '"' in v) else v
        out = [",".join(esc(h) for h in self.headers)]
        out.extend(",".join(esc(c) for c in r) for r in self.rows)
        return "\n".join(out)


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def course_articulation_matrix(
    *,
    course_code: str,
    course_name: str,
    framework_code: str,
    co_codes: list[str],
    co_po_levels: dict[str, dict[str, int]],
    pso_codes: list[str] | None = None,
    co_pso_levels: dict[str, dict[str, int]] | None = None,
) -> Table:
    """SAR Table 1.4.2.1 -- CO rows against PO/PSO columns, values 1/2/3/'-'."""
    fw = get_framework(framework_code)
    psos = pso_codes or []
    headers = ["Course Outcomes (COs)", *fw.po_codes, *psos]
    rows: list[list[str]] = []
    for co in co_codes:
        row = [co]
        row += [render_level(co_po_levels.get(co, {}).get(po, 0)) for po in fw.po_codes]
        row += [render_level((co_pso_levels or {}).get(co, {}).get(p, 0)) for p in psos]
        rows.append(row)
    return Table(
        "1.4.2.1",
        f"Course articulation matrix. Course: {course_name} ({course_code}); "
        f"framework: {fw.name}",
        headers, rows,
    )


def program_articulation_matrix(
    *,
    framework_code: str,
    courses: list[tuple[str, str]],
    course_po_levels: dict[str, dict[str, float | None]],
    pso_codes: list[str] | None = None,
) -> Table:
    """SAR Table 1.5.1 -- one row per course."""
    fw = get_framework(framework_code)
    psos = pso_codes or []
    headers = ["Course Code", "Course Name", *fw.po_codes, *psos]
    rows = []
    for code, name in courses:
        levels = course_po_levels.get(code, {})
        rows.append([code, name, *[_fmt(levels.get(po)) for po in (*fw.po_codes, *psos)]])
    return Table("1.5.1", "Program articulation matrix.", headers, rows)


def po_direct_attainment_table(
    *,
    framework_code: str,
    course_codes: list[str],
    course_po_attainment: dict[str, dict[str, float | None]],
    direct_attainment: dict[str, float | None],
    pso_codes: list[str] | None = None,
) -> Table:
    """
    SAR Table 3.8.1 -- courses x POs, with the 'Direct Attainment' average row
    the SAR requires at the bottom.
    """
    fw = get_framework(framework_code)
    cols = [*fw.po_codes, *(pso_codes or [])]
    headers = ["Course code", *cols]
    rows = [
        [code, *[_fmt(course_po_attainment.get(code, {}).get(c)) for c in cols]]
        for code in course_codes
    ]
    rows.append(["Direct Attainment", *[_fmt(direct_attainment.get(c)) for c in cols]])
    return Table("3.8.1", "PO and PSO attainment value using direct assessment tools.",
                 headers, rows)
