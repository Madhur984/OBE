"""
Internal CO attainment, the way the department computes it.

Their `CO Attainment 2023-24 even.xlsx` is the specification, and this module
was written by reproducing it rather than by reading a definition: every cell
of all eleven subjects across three semesters comes back identical, which is
the only reason to trust it. See `tests/test_co_attainment.py`.

The sheet is a teacher's own marks table -- one row per student, one column
per CO, the max marks for each CO on the row above -- followed by five summary
rows:

    No. of Students Appeared
    Taget T1 = 45%                        <- the mark a student must cross
    No. of Students Crossing T1
    % of Students crossing T1
    CO Attainment (if % of students >= 60% (T2), Att. = 3)
    Internal CO Attainment                <- the mean across that subject's COs

So there are two targets and they are different kinds of thing, which is the
part worth being careful about:

  * **T1 is a mark.** 45% of that CO's max marks. A student at or above it has
    attained the outcome.
  * **T2 is a share of students.** 60%. If that many crossed T1, the course
    scores the full 3.

and the level in between is proportional, not banded:

    attainment = min(3, crossing% / T2 * 3)

This is deliberately *not* the rule in `attainment.py`, which bands a success
percentage into 1/2/3 against three thresholds and works per exam question.
Both are legitimate and they answer different questions; this one is the
department's own and the one their sheet publishes, so it lives on its own
rather than bending the other into a shape it was not built for.

Absent and debarred students are excluded from the denominator, per CO rather
than per course -- their sheet has 135 students appearing for CO1 of a subject
and 176 for CO3 of the same subject, because the internal exams that assessed
them were sat by different people.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# The department's own defaults, taken from their sheet's own labels. Both are
# per-course settings a teacher can change, because a different course may run
# a different scheme -- they are passed in, never assumed here.
DEFAULT_T1_PCT = 45.0        # of the max marks for that CO
DEFAULT_T2_PCT = 60.0        # of the students who appeared
MAX_LEVEL = 3.0

# What a teacher writes where a mark would go. None of these is a zero: a
# student who did not sit the exam is out of the denominator, while a student
# who sat it and scored nothing is a zero that counts.
ABSENT = ("ab", "db", "d", "a", "-", "--", "na", "n/a", "abs", "absent",
          "debarred", "detained")

# Marks are compared at two decimal places. One mark in the department's own
# sheet is stored as 10.799999999999999 against a T1 of 10.8: Excel counts
# that student as having crossed and an exact float comparison does not, and
# the difference moves a published course attainment by a hundredth. Rounding
# both sides first is what makes this reproduce their file exactly.
PLACES = 2


class AttainmentError(ValueError):
    """A marks table this module cannot honestly compute from."""


def parse_mark(value) -> float | None:
    """A mark, or None for absent / debarred / blank.

    Returns None rather than 0.0 on purpose: the two are different facts and
    conflating them silently deflates every course a sick student missed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    text = str(value).strip()
    if not text or text.lower() in ABSENT:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class CoAttainment:
    """One CO's column of the summary, in the order the sheet prints it."""
    co_code: str
    max_marks: float
    appeared: int                   # students with a mark, absent excluded
    target_marks: float             # T1, as a mark
    crossing: int                   # students at or above T1
    crossing_pct: float
    attainment: float               # 0 .. MAX_LEVEL

    def to_dict(self) -> dict:
        return {"co_code": self.co_code, "max_marks": self.max_marks,
                "appeared": self.appeared,
                "target_marks": round(self.target_marks, 2),
                "crossing": self.crossing,
                "crossing_pct": round(self.crossing_pct, 2),
                "attainment": round(self.attainment, 2)}


@dataclass(frozen=True)
class CourseAttainment:
    course_code: str
    t1_pct: float
    t2_pct: float
    cos: list[CoAttainment] = field(default_factory=list)
    students: int = 0               # rows read, however many sat each exam
    ignored: list[str] = field(default_factory=list)

    @property
    def internal(self) -> float | None:
        """The mean over this course's COs -- the sheet's last row."""
        vals = [c.attainment for c in self.cos]
        return sum(vals) / len(vals) if vals else None

    def to_dict(self) -> dict:
        return {"course_code": self.course_code, "t1_pct": self.t1_pct,
                "t2_pct": self.t2_pct, "students": self.students,
                "cos": [c.to_dict() for c in self.cos],
                "internal": None if self.internal is None else round(self.internal, 2),
                "ignored": list(self.ignored)}


def compute_co_attainment(
    marks: dict[str, list],
    max_marks: dict[str, float],
    *,
    course_code: str = "",
    t1_pct: float = DEFAULT_T1_PCT,
    t2_pct: float = DEFAULT_T2_PCT,
    students: int = 0,
) -> CourseAttainment:
    """
    `marks` is {CO code: [one entry per student]}, entries being numbers or the
    absence markers a teacher actually types. `max_marks` is {CO code: max}.

    Every CO named in `max_marks` appears in the result, including one nobody
    sat -- a CO with no marks is a finding, not a row to drop.
    """
    if not 0 < t1_pct <= 100:
        raise AttainmentError("T1 must be a percentage above 0 and at most 100.")
    if not 0 < t2_pct <= 100:
        raise AttainmentError("T2 must be a percentage above 0 and at most 100.")
    if not max_marks:
        raise AttainmentError("No CO has its maximum marks set.")

    rows, ignored = [], []
    for co in max_marks:
        top = float(max_marks[co] or 0)
        if top <= 0:
            ignored.append(f"{co}: no maximum marks, so nothing can be computed")
            continue
        got = [parse_mark(v) for v in (marks.get(co) or [])]
        appeared = [m for m in got if m is not None]
        over = [m for m in appeared if m > top + 10 ** -PLACES]
        if over:
            ignored.append(
                f"{co}: {len(over)} mark(s) above the maximum of {top:g} — "
                f"highest {max(over):g}")
        target = top * t1_pct / 100.0
        crossing = [m for m in appeared
                    if round(m, PLACES) >= round(target, PLACES)]
        pct = (len(crossing) / len(appeared) * 100.0) if appeared else 0.0
        level = min(MAX_LEVEL, pct / t2_pct * MAX_LEVEL)
        rows.append(CoAttainment(co, top, len(appeared), target,
                                 len(crossing), pct, level))
    return CourseAttainment(course_code, t1_pct, t2_pct, rows,
                            students or max((len(v) for v in marks.values()), default=0),
                            ignored)


# --------------------------------------------------- reading a marks table

_CO_HEAD = re.compile(r"^\s*(?:CO|C0)\s*[-–—.:]?\s*(\d{1,2})\s*$", re.I)
# The first column of the summary block the sheet prints under the students.
# Matched so the rows below it are never read as people.
_SUMMARY_LABEL = re.compile(
    r"no\.?\s*of\s*students?\b|\btarget\b|\btaget\b|\bt1\b|\bt2\b|%\s*of\s*students?\b|"
    r"\battainment\b|\baverage\b|\bmean\b|\btotal\b", re.I)
_NAME_HEAD = re.compile(r"name", re.I)
_ROLL_HEAD = re.compile(r"roll|enroll|reg", re.I)


def _co_columns(row: list) -> dict[int, str]:
    """{column index: CO code} for the CO headers in this row."""
    out = {}
    for i, cell in enumerate(row):
        m = _CO_HEAD.match(str(cell or ""))
        if m:
            out[i] = f"CO{int(m.group(1))}"
    return out


def read_marks_table(rows: list[list]) -> list[dict]:
    """
    Find the marks tables in a grid of cells, one per subject block.

    Written against the department's own workbook, whose sheets carry five
    subjects side by side: a row of subject codes, a row of CO headers
    repeating CO1..CO5 per subject, a row of maximum marks, then the students.
    A teacher's own single-subject export is the same shape with one block, so
    both are read by the same code.

    Returns [{course_code, cos: [codes], max_marks: {}, marks: {}, students,
    names: []}] -- never the summary rows, which are recomputed rather than
    trusted.
    """
    header = None
    for r, row in enumerate(rows[:40]):
        found = _co_columns(row)
        # Two CO headers on one line is a marks table; one is a coincidence.
        if len(found) >= 2:
            header = (r, found)
            break
    if header is None:
        raise AttainmentError(
            "No CO columns found. The sheet needs a row of headers reading "
            "CO1, CO2, … above the marks.")
    hrow, cols = header

    name_col = roll_col = None
    for i, cell in enumerate(rows[hrow]):
        text = str(cell or "")
        if name_col is None and _NAME_HEAD.search(text):
            name_col = i
        if roll_col is None and _ROLL_HEAD.search(text):
            roll_col = i

    # The maximum marks sit on the first row under the header that is numeric
    # in the CO columns and carries no student name.
    def text_at(row: list, i: int | None) -> str:
        """A cell as text, where an empty cell is empty rather than "None".

        Named apart from any loop variable on purpose: an earlier version was
        called `cell`, which the header scan below rebinds, and the helper
        became whatever the last header cell happened to be.
        """
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    max_row = None
    for r in range(hrow + 1, min(hrow + 4, len(rows))):
        vals = [parse_mark(rows[r][i] if i < len(rows[r]) else None) for i in cols]
        if any(v is not None for v in vals) and not text_at(rows[r], name_col):
            max_row = r
            break
    if max_row is None:
        raise AttainmentError(
            "No row of maximum marks found under the CO headers.")

    # Subject codes, where the sheet carries them above the CO headers.
    labels: dict[int, str] = {}
    if hrow > 0:
        for i, cell in enumerate(rows[hrow - 1]):
            text = str(cell or "").strip()
            if text and i in cols:
                labels[i] = text

    # Split the CO columns into blocks: a new block starts at a labelled
    # column, or wherever the CO numbering restarts.
    blocks, current = [], None
    last_no = 0
    for i in sorted(cols):
        no = int(cols[i][2:])
        starts = i in labels or no <= last_no or current is None
        if starts:
            current = {"course_code": labels.get(i, ""), "cols": []}
            blocks.append(current)
        current["cols"].append(i)
        last_no = no

    out = []
    for b in blocks:
        max_marks, marks = {}, {}
        for i in b["cols"]:
            co = cols[i]
            top = parse_mark(rows[max_row][i] if i < len(rows[max_row]) else None)
            if top is None:
                continue            # an empty template block
            max_marks[co] = top
            marks[co] = [row[i] if i < len(row) else None
                         for row in rows[max_row + 1:]]
        if not max_marks:
            continue
        names = [text_at(row, name_col) for row in rows[max_row + 1:]]
        # Where the students stop.
        #
        # The obvious test -- "the last row with a mark in it" -- is wrong on
        # the department's own sheet, because the five summary rows beneath
        # the students are full of numbers: the appeared count, T1, the
        # crossing count, the percentage and the attainment all sit in the CO
        # columns. Counting those as six more students put 182 students in a
        # class of 176 and moved every published figure.
        #
        # A student has a name. Where the sheet has a name column, that is the
        # test; where it has none, fall back to the marks and stop at the
        # first row whose first cell reads like one of the summary labels.
        body = rows[max_row + 1:]
        keep = 0
        for n, row in enumerate(body):
            if _SUMMARY_LABEL.search(text_at(row, 0)):
                break
            if name_col is not None:
                if not (n < len(names) and names[n]):
                    continue
            elif not any(parse_mark(row[i] if i < len(row) else None) is not None
                         for i in b["cols"]):
                continue
            keep = n + 1
        for co in marks:
            marks[co] = marks[co][:keep]
        out.append({"course_code": b["course_code"], "cos": list(max_marks),
                    "max_marks": max_marks, "marks": marks,
                    "students": keep, "names": names[:keep]})
    if not out:
        raise AttainmentError("CO columns were found but none carried maximum marks.")
    return out
