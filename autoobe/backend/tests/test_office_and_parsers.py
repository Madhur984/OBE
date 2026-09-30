"""Word/Excel rendering, DOCX question papers, and Excel marks sheets."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.engine import docx_parser as DP
from app.engine import office_export as OX
from app.engine.sar_export import Table

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def tables():
    return [
        Table("1.4.2.1", "Course articulation matrix. Course: DBMS (KCS501)",
              ["Course Outcomes (COs)", "PO1", "PO2", "PO3", "PSO1"],
              [["CO1", "3", "2", "-", "2"], ["CO2", "2", "3", "3", "3"]]),
        Table("3.8.1", "PO and PSO attainment value using direct assessment tools.",
              ["Course code", "PO1", "PO2"],
              [["KCS501", "2.40", "2.10"], ["Direct Attainment", "2.40", "2.10"]]),
    ]


# ---------------------------------------------------------------------------
# Word / Excel export
# ---------------------------------------------------------------------------

def test_docx_is_a_real_openable_document(tables):
    data = OX.tables_to_docx(tables, meta=OX.DocumentMeta(
        institution="Demo Institute", program="B.Tech CSE",
        framework="GAPC v4.0", academic_year="2025-26"))
    assert data[:2] == b"PK", "A .docx is a zip container."
    doc = docx.Document(io.BytesIO(data))
    assert len(doc.tables) >= 3          # info table + two SAR tables


def test_docx_preserves_every_cell(tables):
    doc = docx.Document(io.BytesIO(OX.tables_to_docx(tables)))
    matrix = next(t for t in doc.tables
                  if t.rows[0].cells[0].text == "Course Outcomes (COs)")
    body = [[c.text for c in r.cells] for r in matrix.rows[1:]]
    assert body == [["CO1", "3", "2", "-", "2"], ["CO2", "2", "3", "3", "3"]]


def test_docx_carries_the_dash_is_not_zero_caveat(tables):
    doc = docx.Document(io.BytesIO(OX.tables_to_docx(tables)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "distinct from a zero" in text


def test_docx_includes_methodology_notes(tables):
    data = OX.tables_to_docx(tables, notes=["80:20 direct:indirect is institutional policy."])
    doc = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "institutional policy" in text


def test_xlsx_writes_numbers_as_numbers(tables):
    data = OX.tables_to_xlsx(tables)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb[[s for s in wb.sheetnames if s.startswith("3.8.1")][0]]
    assert ws.cell(row=4, column=2).value == 2.40
    assert isinstance(ws.cell(row=4, column=2).value, float), \
        "A college must be able to check the arithmetic in Excel."


def test_xlsx_keeps_a_dash_as_text(tables):
    wb = openpyxl.load_workbook(io.BytesIO(OX.tables_to_xlsx(tables)))
    ws = wb[[s for s in wb.sheetnames if s.startswith("1.4.2.1")][0]]
    assert ws.cell(row=4, column=4).value == "-", "'-' is not a value."


def test_xlsx_has_a_cover_sheet_listing_contents(tables):
    wb = openpyxl.load_workbook(io.BytesIO(OX.tables_to_xlsx(
        tables, meta=OX.DocumentMeta(program="B.Tech CSE"))))
    assert wb.sheetnames[0] == "Cover"
    values = [c.value for row in wb["Cover"].iter_rows() for c in row]
    assert "B.Tech CSE" in values
    assert any(v and str(v).startswith("Table 1.4.2.1") for v in values)


def test_sheet_names_stay_within_excel_limits():
    long_table = Table("1.5.1", "x" * 80, ["A"], [["1"]])
    wb = openpyxl.load_workbook(io.BytesIO(OX.tables_to_xlsx([long_table])))
    assert all(len(n) <= 31 for n in wb.sheetnames)


def test_evidence_bundle_renders_to_word():
    md = ("# Evidence bundle — KCS501\n\n## Mapping\n\n"
          "| CO | Target | Level |\n| --- | --- | --- |\n| CO1 | PO1 | 3 |\n")
    doc = docx.Document(io.BytesIO(OX.evidence_bundle_to_docx(md)))
    assert doc.tables[0].rows[1].cells[1].text == "PO1"
    assert "Evidence bundle" in doc.paragraphs[0].text


def test_formats_available_reports_honestly():
    assert OX.formats_available() == {"docx": True, "xlsx": True}


# ---------------------------------------------------------------------------
# Question paper parsing
# ---------------------------------------------------------------------------

AKTU_PAPER = """
Printed Pages: 2                                    Sub Code: KCS501
Roll No.
B.TECH (SEM V) THEORY EXAMINATION 2024-25
DATABASE MANAGEMENT SYSTEM
Time: 3 Hours                                       Total Marks: 100

SECTION A
1. Attempt all questions in brief.                                    (2 x 10 = 20)
(a) Define a database schema. [2]
(b) What is a foreign key? [2]

SECTION B
2. Attempt any three of the following:                                (10 x 3 = 30)
(a) Explain the three-schema architecture of a DBMS. [10]
(b) Draw an ER diagram for a library system. [10]
(c) Write SQL queries to find the second highest salary. [10]
(d) Determine whether schedule S is conflict serializable. [10]
(e) Compare two-phase locking with timestamp ordering. [10]
"""


def test_sections_and_questions_are_recognised():
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    numbers = [q.number for q in paper.questions]
    assert "Q1a" in numbers and "Q2c" in numbers
    assert len(paper.questions) == 7


def test_marks_are_extracted_from_brackets():
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    by_no = {q.number: q for q in paper.questions}
    assert by_no["Q1a"].max_marks == 2
    assert by_no["Q2a"].max_marks == 10


def test_attempt_any_three_creates_a_choice_group():
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    optional = [q for q in paper.questions if q.is_optional]
    assert len(optional) == 5, "All five section-B parts are alternatives."
    assert {q.choose_count for q in optional} == {3}
    assert all(q.number.startswith("Q2") for q in optional)


def test_compulsory_questions_are_not_grouped():
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    by_no = {q.number: q for q in paper.questions}
    assert by_no["Q1a"].choice_group is None


def test_total_marks_counts_only_what_a_student_can_earn():
    """
    Summing every printed question is how an out-of-100 paper totals 170.
    Section A is 2x2=4 here; Section B is any three of five tens = 30.
    """
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    assert paper.total_marks == 34


def test_noise_lines_are_dropped():
    paper = DP.parse_question_paper_text(AKTU_PAPER)
    text = " ".join(q.text for q in paper.questions)
    assert "Printed Pages" not in text
    assert "Roll No" not in text


def test_choose_all_of_a_group_is_treated_as_compulsory():
    paper = DP.parse_question_paper_text(
        "2. Attempt any two of the following:\n"
        "(a) First question here. [10]\n"
        "(b) Second question here. [10]\n"
    )
    assert all(not q.is_optional for q in paper.questions)
    assert any("treated as compulsory" in w for w in paper.warnings)


def test_inline_co_and_bloom_tags_are_read():
    paper = DP.parse_question_paper_text(
        "1. Explain the ACID properties of a transaction. [10] CO4 K2\n"
    )
    q = paper.questions[0]
    assert q.co_code == "CO4"
    assert q.bloom_level == 2
    assert "CO4" not in q.text and "K2" not in q.text


def test_missing_marks_are_reported_not_guessed():
    paper = DP.parse_question_paper_text(
        "1. Explain the three-schema architecture.\n"
        "2. Draw an ER diagram for a library.\n"
    )
    assert all(q.max_marks is None for q in paper.questions)
    assert any("marks-weighted" in w for w in paper.warnings)


def test_empty_input_says_so():
    paper = DP.parse_question_paper_text("")
    assert paper.confidence == "none"
    assert any("No questions were recognised" in w for w in paper.warnings)
    assert any("OCR" in w for w in paper.warnings)


def test_duplicate_numbers_are_disambiguated():
    paper = DP.parse_question_paper_text(
        "1. First question about databases. [5]\n1. Another question entirely. [5]\n"
    )
    assert len({q.number for q in paper.questions}) == 2


def _docx_with_table(rows: list[list[str]]) -> bytes:
    d = docx.Document()
    t = d.add_table(rows=0, cols=len(rows[0]))
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_docx_question_grid_is_preferred_over_prose():
    data = _docx_with_table([
        ["Q. No.", "Question", "Marks", "CO", "KL"],
        ["1", "Explain the three-schema architecture of a DBMS.", "10", "CO1", "K2"],
        ["2", "Draw an ER diagram for a library management system.", "10", "CO2", "K3"],
        ["3", "Determine whether schedule S is conflict serializable.", "10", "CO4", "K4"],
    ])
    paper = DP.parse_question_paper_docx(data)
    assert paper.source == "docx-table"
    assert paper.confidence == "high"
    assert [q.co_code for q in paper.questions] == ["CO1", "CO2", "CO4"]
    assert [q.bloom_level for q in paper.questions] == [2, 3, 4]
    assert paper.total_marks == 30


def test_docx_without_a_grid_falls_back_to_prose():
    d = docx.Document()
    for line in AKTU_PAPER.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    paper = DP.parse_question_paper_docx(buf.getvalue())
    assert paper.source == "docx"
    assert len(paper.questions) == 7


# ---------------------------------------------------------------------------
# Excel marks
# ---------------------------------------------------------------------------

def _xlsx(rows, sheet="Sheet1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_marks_are_read_through_the_shared_detector():
    data = _xlsx([
        ["Roll No", "Name", "Q1", "Q2", "Q3", "Total"],
        ["21CS001", "A Sharma", 8, 7, 9, 24],
        ["21CS002", "B Verma", 6, 9, 5, 20],
        ["21CS003", "C Gupta", 9, "AB", 7, 16],
    ])
    result = DP.parse_marks_xlsx(data)
    assert result.student_count == 3
    labels = [c.question_label for c in result.question_columns]
    assert labels == ["Q1", "Q2", "Q3"], "Name and Total must be ignored."


def test_xlsx_absentee_is_excluded_not_zeroed():
    data = _xlsx([
        ["Roll No", "Q1"],
        ["21CS001", 8],
        ["21CS002", "AB"],
    ])
    result = DP.parse_marks_xlsx(data)
    from app.engine.ingest import to_question_payload
    payload, problems = to_question_payload(
        result, co_by_question={"Q1": "CO1"}, max_by_question={"Q1": 10})
    assert not problems
    assert payload[0]["student_marks"] == {"21CS001": 8.0}


def test_trailing_total_row_is_skipped():
    data = _xlsx([
        ["Roll No", "Q1"],
        ["21CS001", 8],
        ["21CS002", 6],
        ["Total", 14],
    ])
    result = DP.parse_marks_xlsx(data)
    assert result.student_count == 2
    assert "Total" in result.skipped_rows


def test_named_worksheet_is_selected_and_reported():
    wb = openpyxl.Workbook()
    wb.active.title = "Instructions"
    wb.active.append(["Do not edit this sheet"])
    ws = wb.create_sheet("Sessional 1")
    ws.append(["Roll No", "Q1"])
    ws.append(["21CS001", 8])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    assert DP.xlsx_sheet_names(data) == ["Instructions", "Sessional 1"]
    result = DP.parse_marks_xlsx(data, sheet="Sessional 1")
    assert result.student_count == 1
    assert "Sessional 1" in result.warnings[0]


def test_empty_worksheet_says_so():
    result = DP.parse_marks_xlsx(_xlsx([]))
    assert result.student_count == 0
    assert "empty" in result.warnings[0].lower()
