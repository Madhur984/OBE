"""
Question papers from .docx, marks sheets from .xlsx.

These are the last two places the product asked a human to retype something
that already existed in a file. Both are worth doing properly rather than
approximately, for different reasons:

**Question papers** carry the choice structure -- "attempt any two of the
following" -- and that structure changes the attainment arithmetic, not just
the layout (see `attainment.AttemptPolicy`). A parser that reads the questions
but drops the word "any" produces numbers that are wrong in a direction nobody
notices. So choice detection is the part with the most care in it here.

**Marks sheets** arrive as .xlsx far more often than .csv, and asking faculty
to "save as CSV" first is precisely the kind of small indignity that makes a
tool go unused. The sheet is flattened to delimited text and handed to the
existing detector in `ingest.py`, so both paths share one column-detection
implementation and one set of tests.

Both readers are optional-dependency-guarded and degrade to a clear message.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.engine.ingest import IngestResult, ingest_marks

try:
    import docx  # python-docx
    _DOCX = True
except Exception:                                     # pragma: no cover
    _DOCX = False

try:
    import openpyxl
    _XLSX = True
except Exception:                                     # pragma: no cover
    _XLSX = False


class ParserUnavailable(RuntimeError):
    """The reader for this file type is not installed."""


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# "Attempt any TWO of the following", "Answer any 3 parts", "Attempt any one
# part of the following". AKTU papers phrase this half a dozen ways.
_CHOICE = re.compile(
    r"\b(?:attempt|answer|solve|do)\s+any\s+"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
    re.I,
)
# A section heading: "SECTION A", "Section-B", "PART C"
_SECTION = re.compile(r"^\s*(?:section|part)\s*[-–:]?\s*([A-Da-d1-4])\b", re.I)

# Question numbering: "1.", "Q1", "Q.1", "1 (a)", "(a)", "a)"
_QNUM = re.compile(r"^\s*(?:q\s*\.?\s*)?(\d{1,2})\s*[\.\)\-]?\s*(?:\(([a-z])\)|([a-z])\))?\s+",
                   re.I)
_SUBPART = re.compile(r"^\s*\(?([a-z])\)\s+", re.I)

# Trailing mark allocations: "[10]", "(10 Marks)", "10M", "... 2 x 10 = 20"
_MARKS = re.compile(
    r"[\[\(]\s*(\d{1,3}(?:\.\d)?)\s*(?:marks?|m)?\s*[\]\)]\s*$"
    r"|(?:\bmarks?\s*[:\-]\s*(\d{1,3})\b)"
    r"|(?:\b(\d{1,2})\s*m\b\s*$)",
    re.I,
)
# CO / Bloom tags some universities already print in the paper.
_CO_TAG = re.compile(r"\b(CO\s*\.?\s*(\d{1,2}))\b", re.I)
_KL_TAG = re.compile(r"\bK\s*\.?\s*([1-6])\b")

_NOISE = re.compile(
    r"^\s*(printed pages?|roll\s*no|paper\s*id|sub\s*code|subject code|time\s*[:]|"
    r"total marks|max(imum)?\s*marks|note\s*[:]|instructions?\s*[:]|"
    r"b\.?\s*tech|semester|university|college|institute)\b",
    re.I,
)

# A numbered *instruction*, not a question: "1. Attempt all questions in
# brief.", "2. Attempt any three of the following:". These carry the question
# number that the (a)/(b) parts below them belong to, and they open any choice
# group -- but they are not themselves answerable, and counting them as
# questions both invents an unanswerable item and corrupts the paper total.
_INSTRUCTION = re.compile(
    r"^\s*(?:attempt|answer|solve|do)\s+"
    r"(?:all|any|the\s+following|each|both)\b",
    re.I,
)


@dataclass
class ParsedQuestion:
    number: str
    text: str
    max_marks: float | None = None
    co_code: str | None = None
    bloom_level: int | None = None
    section: str = ""
    choice_group: str | None = None
    choose_count: int | None = None

    @property
    def is_optional(self) -> bool:
        return self.choice_group is not None


@dataclass
class ParsedPaper:
    questions: list[ParsedQuestion] = field(default_factory=list)
    source: str = "text"
    warnings: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if not self.questions:
            return "none"
        if self.source == "docx-table":
            return "high"
        with_marks = sum(1 for q in self.questions if q.max_marks)
        if with_marks >= max(1, len(self.questions) * 3 // 4):
            return "high"
        if with_marks:
            return "medium"
        return "low"

    @property
    def total_marks(self) -> float:
        """
        Sum of marks a student can actually earn: an optional group counts
        only the number of questions the student must answer, taking the
        highest-valued ones. Summing every printed question would overstate
        the paper, which is how "out of 100" papers total 170.
        """
        total = 0.0
        groups: dict[str, list[ParsedQuestion]] = {}
        for q in self.questions:
            if q.choice_group:
                groups.setdefault(q.choice_group, []).append(q)
            else:
                total += q.max_marks or 0.0
        for members in groups.values():
            take = members[0].choose_count or len(members)
            marks = sorted((m.max_marks or 0.0) for m in members)[::-1]
            total += sum(marks[:take])
        return round(total, 2)

    def to_dicts(self) -> list[dict]:
        return [
            {"question_no": q.number, "text": q.text, "max_marks": q.max_marks,
             "co_code": q.co_code, "bloom_level": q.bloom_level,
             "section": q.section, "choice_group": q.choice_group,
             "choose_count": q.choose_count}
            for q in self.questions
        ]


def _extract_marks(line: str) -> tuple[str, float | None]:
    m = _MARKS.search(line)
    if not m:
        return line, None
    value = next((g for g in m.groups() if g), None)
    if value is None:
        return line, None
    cleaned = (line[:m.start()] + line[m.end():]).strip(" .:-")
    try:
        return cleaned, float(value)
    except ValueError:
        return line, None


def _extract_tags(line: str) -> tuple[str, str | None, int | None]:
    co = None
    kl = None
    m = _CO_TAG.search(line)
    if m:
        co = f"CO{int(m.group(2))}"
        line = line[:m.start()] + line[m.end():]
    m = _KL_TAG.search(line)
    if m:
        kl = int(m.group(1))
        line = line[:m.start()] + line[m.end():]
    return line.strip(" .:-|"), co, kl


def parse_question_paper_text(text: str) -> ParsedPaper:
    """
    Parse a paper from plain text (paste, .txt, or PDF extraction).

    Choice handling is the subtle part: "Attempt any two of the following"
    applies to the questions that *follow* it, until the next question that
    starts a new top-level number or a new section. So the marker opens a
    group and the group stays open over subsequent sub-parts.
    """
    paper = ParsedPaper(source="text")
    section = ""
    open_group: str | None = None
    open_choose: int | None = None
    group_seq = 0
    current_top = ""
    last: ParsedQuestion | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        sec = _SECTION.match(line)
        if sec:
            section = sec.group(1).upper()
            open_group, open_choose = None, None
            # A section heading often carries the choice rule on the same line.
            ch = _CHOICE.search(line)
            if ch:
                group_seq += 1
                open_group = f"G{group_seq}"
                token = ch.group(1).lower()
                open_choose = _NUMBER_WORDS.get(token) or int(token)
            continue

        ch = _CHOICE.search(line)
        if ch and not _QNUM.match(line):
            group_seq += 1
            open_group = f"G{group_seq}"
            token = ch.group(1).lower()
            open_choose = _NUMBER_WORDS.get(token) or int(token)
            continue

        if _NOISE.match(line):
            continue

        body, marks = _extract_marks(line)
        body, co, kl = _extract_tags(body)

        qm = _QNUM.match(body)
        sub = _SUBPART.match(body) if not qm else None

        if qm:
            current_top = qm.group(1)
            letter = qm.group(2) or qm.group(3) or ""
            number = f"Q{current_top}{letter.lower()}"
            body = body[qm.end():].strip()

            # "2. Attempt any three of the following:" numbers the parts that
            # follow and may open a choice group, but is not itself a question.
            inline = _CHOICE.search(line)
            if inline:
                group_seq += 1
                open_group = f"G{group_seq}"
                token = inline.group(1).lower()
                open_choose = _NUMBER_WORDS.get(token) or int(token)
            elif not letter:
                # A new compulsory top-level question ends any group a previous
                # sub-part list had opened.
                open_group, open_choose = None, None
            if not body.strip() or _INSTRUCTION.match(body):
                continue
        elif sub:
            letter = sub.group(1).lower()
            number = f"Q{current_top or '1'}{letter}"
            body = body[sub.end():].strip()
        else:
            # A continuation of the previous question's text.
            if last is not None and len(body) > 3:
                last.text = f"{last.text} {body}".strip()
                if marks and last.max_marks is None:
                    last.max_marks = marks
            continue

        if not body:
            continue

        q = ParsedQuestion(
            number=number, text=body, max_marks=marks, co_code=co,
            bloom_level=kl, section=section,
            choice_group=open_group, choose_count=open_choose,
        )
        paper.questions.append(q)
        last = q

    _dedupe_numbers(paper)
    _validate(paper)
    return paper


def _dedupe_numbers(paper: ParsedPaper) -> None:
    seen: dict[str, int] = {}
    for q in paper.questions:
        if q.number in seen:
            seen[q.number] += 1
            q.number = f"{q.number}.{seen[q.number]}"
        else:
            seen[q.number] = 0


def _validate(paper: ParsedPaper) -> None:
    if not paper.questions:
        paper.warnings.append(
            "No questions were recognised. If this is a scanned PDF it needs OCR "
            "first; if it is a Word file, try the .docx directly rather than "
            "pasted text."
        )
        return
    missing = [q.number for q in paper.questions if not q.max_marks]
    if missing:
        paper.warnings.append(
            f"No mark allocation found for {len(missing)} question(s) "
            f"({', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}). "
            f"Attainment is marks-weighted, so these need a value before it "
            f"can be computed."
        )
    for gid in {q.choice_group for q in paper.questions if q.choice_group}:
        members = [q for q in paper.questions if q.choice_group == gid]
        take = members[0].choose_count or 0
        if take and take >= len(members):
            paper.warnings.append(
                f"Choice group {gid} says attempt any {take} of {len(members)} — "
                f"that is every question in the group, so it has been treated as "
                f"compulsory."
            )
            for q in members:
                q.choice_group, q.choose_count = None, None


def parse_question_paper_docx(data: bytes) -> ParsedPaper:
    """
    Read a .docx paper. Tables are tried first: many universities lay papers
    out as a grid with an explicit CO column, and when that is present it is
    far more reliable than anything inferred from prose.
    """
    if not _DOCX:
        raise ParserUnavailable(
            "Reading .docx needs python-docx (`pip install python-docx`). "
            "Paste the paper as text instead — the same parser runs on it."
        )
    document = docx.Document(io.BytesIO(data))

    table_paper = _from_docx_tables(document)
    if table_paper is not None and len(table_paper.questions) >= 3:
        _dedupe_numbers(table_paper)
        _validate(table_paper)
        return table_paper

    lines: list[str] = [p.text for p in document.paragraphs]
    for t in document.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append("  ".join(c for c in cells if c))
    paper = parse_question_paper_text("\n".join(lines))
    paper.source = "docx"
    return paper


def _from_docx_tables(document) -> ParsedPaper | None:
    """
    Pull questions from a grid whose header names a question and marks column.
    Returns None when no table looks like a question grid.
    """
    for table in document.tables:
        if not table.rows:
            continue
        header = [c.text.strip().lower() for c in table.rows[0].cells]
        joined = " ".join(header)
        if not re.search(r"question|q\.?\s*no|particulars", joined):
            continue
        if not re.search(r"mark", joined):
            continue

        def find(*pats: str) -> int | None:
            for i, h in enumerate(header):
                if any(re.search(p, h) for p in pats):
                    return i
            return None

        i_no = find(r"^q\.?\s*no", r"\bno\b", r"^s\.?\s*no")
        i_text = find(r"question", r"particulars", r"description")
        i_marks = find(r"mark")
        i_co = find(r"\bco\b", r"course outcome")
        i_kl = find(r"\bkl\b", r"bloom", r"^k\b", r"level")
        if i_text is None or i_marks is None:
            continue

        paper = ParsedPaper(source="docx-table")
        seq = 0
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if not any(cells):
                continue
            text = cells[i_text] if i_text < len(cells) else ""
            if not text or len(text) < 5:
                continue
            seq += 1
            number = (cells[i_no].strip() if i_no is not None and i_no < len(cells)
                      and cells[i_no].strip() else str(seq))
            if not number.lower().startswith("q"):
                number = f"Q{number}"
            number = re.sub(r"\s+", "", number).rstrip(".")

            marks = None
            if i_marks < len(cells):
                mm = re.search(r"\d{1,3}(?:\.\d)?", cells[i_marks])
                if mm:
                    marks = float(mm.group())

            co = None
            if i_co is not None and i_co < len(cells):
                cm = _CO_TAG.search(cells[i_co]) or re.search(r"(\d{1,2})", cells[i_co])
                if cm:
                    co = f"CO{int(cm.groups()[-1])}"

            kl = None
            if i_kl is not None and i_kl < len(cells):
                km = _KL_TAG.search(cells[i_kl]) or re.search(r"([1-6])", cells[i_kl])
                if km:
                    kl = int(km.groups()[-1])

            cleaned, ctext_co, ctext_kl = _extract_tags(text)
            paper.questions.append(ParsedQuestion(
                number=number, text=cleaned or text, max_marks=marks,
                co_code=co or ctext_co, bloom_level=kl or ctext_kl,
            ))
        if paper.questions:
            return paper
    return None


# ---------------------------------------------------------------------------
# Excel marks
# ---------------------------------------------------------------------------

def xlsx_sheet_names(data: bytes) -> list[str]:
    if not _XLSX:
        raise ParserUnavailable("Reading .xlsx needs openpyxl (`pip install openpyxl`).")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def xlsx_to_text(data: bytes, sheet: str | None = None) -> str:
    """
    Flatten a worksheet to TSV so the existing column detector can read it.

    `data_only=True` reads cached formula *results*, not the formulas -- marks
    sheets are full of =SUM() and the total column has to be recognised as a
    number so it can be ignored. Merged header cells report their value only
    in the top-left cell, so a blank header is filled from the left, which is
    what makes "Q1 | (blank) | (blank)" spanning sub-parts readable.
    """
    if not _XLSX:
        raise ParserUnavailable(
            "Reading .xlsx needs openpyxl (`pip install openpyxl`). "
            "Save the sheet as CSV and paste it instead."
        )
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        rows: list[list[str]] = []
        for r in ws.iter_rows(values_only=True):
            if r is None:
                continue
            cells = ["" if v is None else str(v).strip() for v in r]
            if any(cells):
                rows.append(cells)
    finally:
        wb.close()

    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    # Carry a merged header value rightwards, but only across the header row
    # and only where the column below it holds data.
    header = rows[0]
    for i in range(1, width):
        if not header[i] and header[i - 1]:
            below = [r[i] for r in rows[1:6] if i < len(r)]
            if any(below):
                header[i] = f"{header[i - 1]}_{i}"
    return "\n".join("\t".join(c.replace("\t", " ") for c in r) for r in rows)


def parse_marks_xlsx(data: bytes, sheet: str | None = None) -> IngestResult:
    """Read an .xlsx marks sheet through the shared column detector."""
    text = xlsx_to_text(data, sheet)
    if not text.strip():
        return IngestResult([], [], None, [], ["The worksheet is empty."])
    result = ingest_marks(text)
    if sheet:
        result.warnings.insert(0, f"Read worksheet '{sheet}'.")
    return result
