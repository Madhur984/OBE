"""
Marks and question-paper ingestion with column auto-detection.

The plan is explicit about this: do not ship a template. Faculty have
spreadsheets that already exist, in whatever shape they already exist, and a
tool that demands a specific layout is slower than the Excel evening it claims
to replace.

So: detect roll numbers by pattern, detect question columns by header shape
(Q1, 1(a), Q.1a, Q1a), and surface only what could not be resolved.

Handles CSV, TSV, and clipboard paste. DOCX question-paper table extraction
lives in docx_parser.py, which needs python-docx; this module is dependency
free so marks ingest always works even in a bare install.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

# Indian university roll numbers vary widely, so match structurally rather
# than against any one university's format: mixed letters+digits, or a long
# digit run, and not a bare small integer (that is a mark, not a roll).
_ROLL_PATTERNS = [
    re.compile(r"^[0-9]{2}[a-z]{2,6}[0-9]{2,5}$", re.I),   # 21CS001, 2021CSE1234
    re.compile(r"^[a-z]{2,6}[0-9]{4,10}$", re.I),          # CSE21001
    re.compile(r"^[0-9]{7,12}$"),                          # 2100290100001
    re.compile(r"^[0-9]{2}-[a-z0-9-]{3,}$", re.I),         # 21-CS-001
]

_QUESTION_HEADER = re.compile(
    r"^\s*q?\.?\s*"          # optional Q / Q.
    r"(\d+)"                 # question number
    r"\s*[\.\-)]?\s*"        # optional separator
    r"\(?([a-z])?\)?"        # optional sub-part
    r"\s*$",
    re.I,
)
_MARKS_NOISE = re.compile(r"^(total|sum|marks?|obtained|grand\s*total|%|percentage)\b", re.I)
_NAME_HEADER = re.compile(r"^(name|student\s*name|student)$", re.I)
_ROLL_HEADER = re.compile(r"^(roll|roll\s*no\.?|roll\s*number|enroll(ment)?\s*(no\.?|number)?|"
                          r"reg(istration)?\s*(no\.?|number)?|univ(ersity)?\s*roll|id)$", re.I)


@dataclass
class DetectedColumn:
    index: int
    header: str
    kind: str                 # "roll" | "name" | "question" | "ignored"
    question_label: str | None = None


@dataclass
class IngestResult:
    columns: list[DetectedColumn]
    rows: list[dict[str, str]]           # roll -> {question_label: raw value}
    roll_column: int | None
    question_columns: list[DetectedColumn]
    warnings: list[str] = field(default_factory=list)
    skipped_rows: list[str] = field(default_factory=list)

    @property
    def student_count(self) -> int:
        return len(self.rows)


def _looks_like_roll(value: str) -> bool:
    v = value.strip()
    if not v or len(v) < 3:
        return False
    return any(p.match(v) for p in _ROLL_PATTERNS)


def _sniff_delimiter(text: str) -> str:
    head = "\n".join(text.strip().splitlines()[:5])
    if "\t" in head:
        return "\t"
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _parse_rows(text: str) -> list[list[str]]:
    delim = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text.strip()), delimiter=delim)
    return [[c.strip() for c in row] for row in reader if any(c.strip() for c in row)]


def _header_looks_like_data(row: list[str]) -> bool:
    """A first row of roll numbers means the file has no header at all."""
    return sum(1 for c in row if _looks_like_roll(c)) >= 1


def detect_columns(rows: list[list[str]]) -> tuple[list[DetectedColumn], int]:
    """
    Returns (columns, first_data_row_index). Falls back to positional
    detection when the sheet has no usable header.
    """
    if not rows:
        return [], 0

    has_header = not _header_looks_like_data(rows[0])
    header = rows[0] if has_header else []
    first_data = 1 if has_header else 0
    sample = rows[first_data:first_data + 8]
    width = max(len(r) for r in rows)

    cols: list[DetectedColumn] = []
    qseq = 0
    for i in range(width):
        raw = header[i].strip() if i < len(header) else ""
        values = [r[i].strip() for r in sample if i < len(r) and r[i].strip()]

        if _ROLL_HEADER.match(raw) or (values and sum(_looks_like_roll(v) for v in values) >= max(1, len(values) // 2)):
            cols.append(DetectedColumn(i, raw or f"col{i+1}", "roll"))
            continue
        if _NAME_HEADER.match(raw):
            cols.append(DetectedColumn(i, raw, "name"))
            continue
        if raw and _MARKS_NOISE.match(raw):
            cols.append(DetectedColumn(i, raw, "ignored"))
            continue

        m = _QUESTION_HEADER.match(raw) if raw else None
        if m:
            label = f"Q{m.group(1)}{m.group(2).lower() if m.group(2) else ''}"
            cols.append(DetectedColumn(i, raw, "question", label))
            qseq += 1
            continue

        numeric = [v for v in values if re.fullmatch(r"-?\d+(\.\d+)?", v)]
        if values and len(numeric) >= max(1, len(values) * 2 // 3):
            qseq += 1
            cols.append(DetectedColumn(i, raw or f"col{i+1}", "question", f"Q{qseq}"))
        else:
            cols.append(DetectedColumn(i, raw or f"col{i+1}", "ignored"))

    return cols, first_data


def ingest_marks(text: str) -> IngestResult:
    """Parse a marks sheet, detecting layout rather than demanding one."""
    rows = _parse_rows(text)
    if not rows:
        return IngestResult([], [], None, [], ["The sheet is empty."])

    cols, first_data = detect_columns(rows)
    roll_cols = [c for c in cols if c.kind == "roll"]
    qcols = [c for c in cols if c.kind == "question"]
    warnings: list[str] = []
    skipped: list[str] = []

    if not roll_cols:
        # No recognisable roll column: fall back to the first column and say so.
        if cols:
            cols[0] = DetectedColumn(cols[0].index, cols[0].header, "roll")
            roll_cols = [cols[0]]
            qcols = [c for c in cols if c.kind == "question"]
            warnings.append(
                "No roll-number column was recognised, so the first column is being "
                "used as the identifier. Check the preview before continuing."
            )
        else:
            return IngestResult(cols, [], None, [], ["No columns could be read."])

    roll_idx = roll_cols[0].index
    if len(roll_cols) > 1:
        warnings.append(
            f"{len(roll_cols)} columns look like identifiers; using "
            f"'{roll_cols[0].header}'."
        )
    if not qcols:
        warnings.append("No question columns were recognised — check the header row.")

    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows[first_data:]:
        if roll_idx >= len(r):
            continue
        roll = r[roll_idx].strip()
        if not roll:
            continue
        if _MARKS_NOISE.match(roll):        # trailing "Total" row
            skipped.append(roll)
            continue
        if roll in seen:
            skipped.append(f"{roll} (duplicate)")
            continue
        seen.add(roll)
        rec: dict[str, str] = {"__roll__": roll}
        for c in qcols:
            rec[c.question_label or c.header] = r[c.index].strip() if c.index < len(r) else ""
        parsed.append(rec)

    return IngestResult(cols, parsed, roll_idx, qcols, warnings, skipped)


def to_question_payload(
    result: IngestResult,
    *,
    co_by_question: dict[str, str],
    max_by_question: dict[str, float],
) -> tuple[list[dict], list[str]]:
    """
    Convert an IngestResult into the /api/attainment question payload,
    reporting any cell that could not be read as a number.
    """
    problems: list[str] = []
    payload: list[dict] = []
    for col in result.question_columns:
        label = col.question_label or col.header
        marks: dict[str, float] = {}
        for row in result.rows:
            raw = (row.get(label) or "").strip()
            if raw == "" or raw.upper() in {"A", "AB", "ABS", "-", "NA"}:
                continue                      # absent: excluded, not zero
            try:
                marks[row["__roll__"]] = float(raw)
            except ValueError:
                problems.append(f"{row['__roll__']} / {label}: cannot read {raw!r}")
        payload.append({
            "question_no": label,
            "co_code": co_by_question.get(label, ""),
            "max_marks": max_by_question.get(label, 0.0),
            "student_marks": marks,
        })
    return payload, problems


def detect_outliers(payload: list[dict]) -> list[str]:
    """
    Flag data-entry errors rather than student performance: a zero on one
    question alongside full marks everywhere else is almost always a
    transcription slip, not a student.
    """
    flags: list[str] = []
    by_student: dict[str, list[tuple[str, float, float]]] = {}
    for q in payload:
        mx = q.get("max_marks") or 0
        for roll, m in q["student_marks"].items():
            by_student.setdefault(roll, []).append((q["question_no"], m, mx))

    for roll, entries in by_student.items():
        if len(entries) < 3:
            continue
        ratios = [(qn, (m / mx) if mx else 0.0) for qn, m, mx in entries]
        zeros = [qn for qn, r in ratios if r == 0.0]
        strong = [r for _, r in ratios if r >= 0.8]
        if len(zeros) == 1 and len(strong) >= len(ratios) - 1:
            flags.append(
                f"{roll}: zero on {zeros[0]} but at least 80% on every other question "
                f"— check the entry."
            )
    return flags
