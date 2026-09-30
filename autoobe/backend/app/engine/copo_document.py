"""
The CO -> PO articulation matrix as a document, with the PO averages.

The average row is the line a peer team reads first and the one institutions
most often compute inconsistently, so this module is explicit about it. There
are two defensible denominators and they give different numbers:

  * **Average over mapped COs** -- the sum of the levels divided by how many
    COs actually map to that PO. This is the AKTU/NBA articulation-matrix
    convention and the figure carried into SAR Table 1.4.2.1.
  * **Average over all COs** -- the same sum divided by every CO in the
    course, counting an unmapped cell as 0.

A PO touched by one CO at level 3 averages 3.00 the first way and 0.60 the
second across five COs. Reporting one without saying which invites the
question "out of how many?", so both are in the document with the denominator
printed beside them.

Nothing here rounds silently: the raw mean is shown to two decimals and the
rounded whole number sits next to it, because some templates want the integer
and a reader must be able to see it was 2.5 before it became 3.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.sar_export import Table


@dataclass(frozen=True)
class PoAverage:
    po_code: str
    po_title: str
    po_action_level: int
    mapped_cos: int                 # COs with a non-zero level against this PO
    total_cos: int
    total_level: int
    level_counts: dict[int, int]    # {1: n, 2: n, 3: n}

    @property
    def average_over_mapped(self) -> float | None:
        return self.total_level / self.mapped_cos if self.mapped_cos else None

    @property
    def average_over_all(self) -> float:
        return self.total_level / self.total_cos if self.total_cos else 0.0

    @property
    def rounded(self) -> int | None:
        """The whole number some SAR templates carry. Never applied silently."""
        a = self.average_over_mapped
        return None if a is None else int(a + 0.5)

    @property
    def is_covered(self) -> bool:
        return self.mapped_cos > 0


def po_averages(co_po_levels: dict[str, dict[str, int]], *,
                po_codes: list[str],
                po_titles: dict[str, str] | None = None,
                po_action_levels: dict[str, int] | None = None,
                co_codes: list[str] | None = None) -> list[PoAverage]:
    """
    One PoAverage per PO, in framework order, including the untouched ones --
    an empty PO column is a finding, so it has to appear rather than vanish.
    """
    titles = po_titles or {}
    action = po_action_levels or {}
    cos = list(co_codes if co_codes is not None else co_po_levels.keys())
    out: list[PoAverage] = []
    for po in po_codes:
        total = mapped = 0
        counts = {1: 0, 2: 0, 3: 0}
        for co in cos:
            lv = int((co_po_levels.get(co) or {}).get(po, 0) or 0)
            if lv <= 0:
                continue
            if lv not in counts:
                raise ValueError(f"{co} -> {po}: mapping level {lv} is not 1, 2 or 3")
            mapped += 1
            total += lv
            counts[lv] += 1
        out.append(PoAverage(po, titles.get(po, ""), int(action.get(po, 0)),
                             mapped, len(cos), total, counts))
    return out


def _fmt(v: float | None, places: int = 2, dash: str = "-") -> str:
    return dash if v is None else f"{v:.{places}f}"


def course_label(course_code: str = "", course_title: str = "") -> str:
    """
    "Digital System Design (BEC-302)" -- title first, code in brackets.

    This is the department's own convention in their mapping workbook, and it
    is the string that both opens and closes a course block, so a reader
    scanning a stacked sheet uses it to find the block boundaries.
    """
    title = (course_title or "").strip()
    code = (course_code or "").strip()
    if title and code:
        return f"{title} ({code})"
    return title or code or "Course"


def course_block_table(cos: list[dict], co_levels: dict[str, dict[str, int]], *,
                       codes: list[str],
                       course_code: str = "", course_title: str = "",
                       number: str = "1", blank: str = "",
                       co_prefix: str = "",
                       convention_codes: tuple[str, ...] = ()) -> Table:
    """
    One course block in the shape a department's own mapping workbook uses.

        BEC-302 Digital System Design | PO1 | PO2 | … | PO12 | PSO1 | PSO2
        CO1 Perform numerous arith…   |  3  |  3  |     3    |   3  |   2
        …
        BEC-302 Digital System Design | 3.00| 3.00|   3.00   | 3.00 | 2.20

    Three details are parity requirements, not styling. The corner cell
    carries the course, not the word "CO". An unmapped cell is **empty** --
    never 0, which reads as a scored zero, and never the "-" that
    `articulation_table` uses. And the average row repeats the course title
    rather than saying "Average", because that is what the sheet does and a
    reader scanning 44 stacked blocks uses it to find the block boundary.
    """
    label = course_label(course_code, course_title)
    co_codes = [str(c.get("code", "")) for c in cos]
    avgs = po_averages(co_levels, po_codes=list(codes), co_codes=co_codes)

    # `convention_codes` is accepted and deliberately not printed in the
    # header. This block is the department's own sheet reproduced, and a mark
    # against half its columns is an annotation on their document, not part of
    # it. Where the cells came from is stated once, in the method note.
    headers = [label] + list(codes)
    rows: list[list[str]] = []
    for n, co in enumerate(cos, start=1):
        code = str(co.get("code", ""))
        statement = str(co.get("statement", "")).strip()
        # A department numbers its outcomes C204.1, C204.2 … where 204 is its
        # own course serial. That number is bookkeeping the syllabus does not
        # carry, so it is supplied rather than guessed; without it the CO's
        # own code is used.
        shown = f"{co_prefix}.{n}" if co_prefix else code
        rows.append(
            [f"{shown} {statement}".strip()]
            + [str(lv) if (lv := int((co_levels.get(code) or {}).get(col, 0) or 0))
               else blank for col in codes]
        )
    rows.append([label] + [_fmt(a.average_over_mapped, dash=blank) for a in avgs])

    return Table(number=number, title=f"CO - PO mapping - {label}",
                 headers=headers, rows=rows,
                 summary_rows=(len(rows) - 1,))


def articulation_table(co_po_levels: dict[str, dict[str, int]], *,
                       po_codes: list[str],
                       co_codes: list[str] | None = None,
                       course_code: str = "",
                       number: str = "1") -> Table:
    """The matrix itself, with the average row appended in the usual place."""
    cos = list(co_codes if co_codes is not None else co_po_levels.keys())
    avgs = po_averages(co_po_levels, po_codes=po_codes, co_codes=cos)
    rows = [
        [co] + [str((co_po_levels.get(co) or {}).get(po, 0) or "-") for po in po_codes]
        for co in cos
    ]
    label = f"{course_code} average".strip() if course_code else "Average"
    rows.append([label] + [_fmt(a.average_over_mapped) for a in avgs])
    suffix = f" - {course_code}" if course_code else ""
    return Table(number=number, title=f"CO - PO articulation matrix{suffix}",
                 headers=["CO"] + list(po_codes), rows=rows)


def averages_table(avgs: list[PoAverage], *, number: str = "2",
                   convention_codes: tuple[str, ...] = ()) -> Table:
    """
    The PO averages on their own, with both denominators visible.

    `Levels` spells out the distribution behind the mean, because 1+3 and 2+2
    both average 2.00 and they are not the same matrix.
    """
    rows = []
    for a in avgs:
        dist = ", ".join(f"{n}x{lv}" for lv, n in sorted(a.level_counts.items())
                         if n) or "-"
        rows.append([
            a.po_code,
            a.po_title,
            str(a.po_action_level) if a.po_action_level else "-",
            f"{a.mapped_cos}/{a.total_cos}", dist, str(a.total_level),
            _fmt(a.average_over_mapped),
            "-" if a.rounded is None else str(a.rounded),
            _fmt(a.average_over_all),
        ])
    return Table(number=number, title="Programme outcome averages",
                 headers=["PO / PSO", "Outcome", "PL", "COs mapped", "Levels", "Total",
                          "Avg (mapped)", "Rounded", "Avg (all COs)"],
                 rows=rows)


def blooms_table(cos: list[dict], *, number: str = "3") -> Table:
    """
    The Bloom's levels the matrix was derived from, and whether each was the
    university's or ours. A reader cannot check a mapping level without this:
    CL comes from the K level, and CL decides the level under Rule 2.
    """
    rows = []
    for co in cos:
        ks = [int(k) for k in (co.get("blooms_levels") or [])]
        cl = max(ks, default=0)
        rows.append([
            str(co.get("code", "")),
            str(co.get("statement", "")),
            ", ".join(f"K{k}" for k in ks) or "-",
            str((cl + 1) // 2) if cl else "-",
            "inferred from the verb" if co.get("bloom_inferred")
            else "declared by the syllabus",
        ])
    return Table(number=number, title="Course outcomes and Bloom's levels",
                 headers=["CO", "Statement", "Bloom's", "CL", "Level source"],
                 rows=rows)


def coverage_notes(avgs: list[PoAverage]) -> list[str]:
    """Plain statements of what the matrix does not cover. Never silent."""
    notes: list[str] = []
    empty = [a.po_code for a in avgs if not a.is_covered]
    if empty:
        notes.append(
            f"{len(empty)} programme outcome(s) are addressed by no course "
            f"outcome: {', '.join(empty)}. An empty column is normal for a "
            f"single course and is a finding only at programme level.")
    thin = [a.po_code for a in avgs if a.mapped_cos == 1 and a.total_cos > 2]
    if thin:
        notes.append(
            f"Averaged over a single course outcome: {', '.join(thin)}. The mean "
            f"equals that one cell and carries no more evidence than it does.")
    return notes


def convention_note(codes: tuple[str, ...] | list[str]) -> str:
    """
    One sentence, at the foot of the method notes, naming the columns the
    department maps as policy for every outcome of a course.

    It is not a warning and it is not printed against the columns themselves.
    The matrix above is the department's own document; this says how it was
    filled, in the same place the two averaging conventions are explained.
    """
    return (
        f"{', '.join(codes)} are mapped for every outcome as departmental "
        f"policy, which is the department's judgement about the programme "
        f"rather than a reading of the individual outcome. The level in each "
        f"cell is still derived by Rule 1 and Rule 2 from that outcome's own "
        f"Bloom's level, so these columns differentiate rather than repeat."
    )


METHOD_NOTE = (
    "Mapping levels are derived, not entered: Rule 1 turns the reviewed "
    "competency (0 none / 1 partial / 2 strong) into a level, and for a strong "
    "match Rule 2 compares the outcome's action level CL against the "
    "programme outcome's PL -- CL >= PL gives 3, CL < PL gives CL. "
    "\"Avg (mapped)\" divides by the number of COs that map to that PO; "
    "\"Avg (all COs)\" divides by every CO in the course."
)


def build_copo_document(*, course_code: str, course_title: str,
                        framework_code: str, po_codes: list[str],
                        po_titles: dict[str, str],
                        po_action_levels: dict[str, int],
                        cos: list[dict],
                        co_po_levels: dict[str, dict[str, int]],
                        convention_codes: tuple[str, ...] = (),
                        extra_notes: tuple[str, ...] = (),
                        ) -> tuple[list[Table], list[str]]:
    """Returns (tables, notes), ready for docx, xlsx, markdown or CSV."""
    co_codes = [str(c.get("code", "")) for c in cos]
    avgs = po_averages(co_po_levels, po_codes=po_codes, po_titles=po_titles,
                       po_action_levels=po_action_levels, co_codes=co_codes)
    tables = [
        articulation_table(co_po_levels, po_codes=po_codes, co_codes=co_codes,
                           course_code=course_code, number="1"),
        averages_table(avgs, number="2", convention_codes=convention_codes),
        blooms_table(cos, number="3"),
    ]
    notes = [METHOD_NOTE, *extra_notes]
    if convention_codes:
        notes.append(convention_note(convention_codes))
    return tables, [*notes, *coverage_notes(avgs)]
