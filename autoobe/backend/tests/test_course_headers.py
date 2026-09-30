"""
Course-header shapes, and the "Untitled course" failure.

A whole-semester PDF holds a dozen courses. When a course's header line is not
recognised, its outcomes still parse — they just arrive with no code and no
title, and the UI shows a card reading "Untitled". With four of those on
screen a reader cannot tell which course is which, so the cost of a missed
header is much higher than it looks.

These pin the shapes that must keep working, the two that were broken, and —
just as important — the cases where recovering a name would be a guess and the
parser must leave it blank instead.
"""
from __future__ import annotations

import pytest

from app.engine.syllabus_parser import parse_text, repair_pdf_text

OUTCOMES = """Course Outcomes: At the end of this course students will be able to:
1. Explain the fundamentals of wireless propagation and cellular concepts.
2. Analyze the performance of voice coding and diversity techniques.
3. Apply knowledge of wireless transmission to equalization problems.
4. Examine the performance of GSM and CDMA cellular systems.
5. Describe mobile adhoc networks and upcoming data communication networks.
"""


def one(header: str):
    """Parse a single course and return it."""
    courses = parse_text(header.rstrip() + "\n" + OUTCOMES).courses
    assert len(courses) == 1, f"expected 1 course, got {len(courses)}"
    return courses[0]


@pytest.mark.parametrize("header,code,title,ltp", [
    ("BEC071 Wireless and Mobile Communication 3L:0T:0P 3 Credits",
     "BEC071", "Wireless and Mobile Communication", (3, 0, 0)),
    ("BEC071 Wireless and Mobile Communication 3-0-0 3 Credits",
     "BEC071", "Wireless and Mobile Communication", (3, 0, 0)),
    ("BEC071 Wireless and Mobile Communication 3 Credits 3L:0T:0P",
     "BEC071", "Wireless and Mobile Communication", (3, 0, 0)),
    ("Wireless and Mobile Communication (BEC071)",
     "BEC071", "Wireless and Mobile Communication", None),
    ("Course Code: BEC071\nCourse Title: Wireless and Mobile Communication",
     "BEC071", "Wireless and Mobile Communication", None),
    ("BEC071 | Wireless and Mobile Communication | 3 | 0 | 0 | 3",
     "BEC071", "Wireless and Mobile Communication", None),
    # Code punctuation and spacing varies by university; it normalises away.
    ("KCS-501 Database Management System 3L:0T:0P 3 Credits",
     "KCS501", "Database Management System", (3, 0, 0)),
    ("BCS 501 Database Management System 3L:0T:0P 3",
     "BCS501", "Database Management System", (3, 0, 0)),
    ("MTCS1011 Advanced Algorithms 3L:0T:0P 3 Credits",
     "MTCS1011", "Advanced Algorithms", (3, 0, 0)),
])
def test_header_shapes_that_must_keep_working(header, code, title, ltp):
    c = one(header)
    assert (c.code, c.title, c.ltp) == (code, title, ltp)


def test_an_all_caps_header_keeps_its_title():
    """
    The banner guard exists to drop "DEPARTMENT OF ELECTRONICS AND
    COMMUNICATION ENGINEERING". It was also dropping "BEC073 MICROWAVE AND
    RADAR ENGINEERING", which left the course with a code and no name —
    plenty of schemes print titles in caps.
    """
    c = one("BEC073 MICROWAVE AND RADAR ENGINEERING")
    assert c.code == "BEC073"
    assert c.title == "MICROWAVE AND RADAR ENGINEERING"


def test_a_real_banner_is_still_not_a_course():
    """The guard must keep doing its job: no code, so not a header."""
    c = one("DEPARTMENT OF ELECTRONICS AND COMMUNICATION ENGINEERING")
    assert c.code is None


def test_a_year_in_a_banner_is_not_read_as_a_course_code():
    """"AKTU 2026" has digits but is not a course code."""
    c = one("AKTU 2026 CURRICULUM AND EVALUATION SCHEME")
    assert c.code is None


def test_a_second_course_does_not_inherit_the_first_ones_identity():
    """
    The failure this whole module guards: merging one course's outcomes with
    another's is invisible in the output and corrupts both matrices.
    """
    text = (
        "BEC071 Wireless and Mobile Communication 3L:0T:0P 3 Credits\n"
        + OUTCOMES
        + "\nSome intervening text about references.\n"
        + OUTCOMES.replace("wireless propagation and cellular concepts",
                           "microwave transmission lines and waveguides")
    )
    courses = parse_text(text).courses
    assert len(courses) == 2
    assert courses[0].code == "BEC071"
    # Nothing nameable preceded the second block, so it stays blank rather
    # than borrowing BEC071's identity.
    assert courses[1].code is None
    assert courses[1].code is None and courses[1].title is None
    assert courses[0].cos[0].statement != courses[1].cos[0].statement


def test_the_real_scheme_still_parses_unchanged():
    """The rescue paths must not disturb a document that parses cleanly."""
    from pathlib import Path

    from app.engine.syllabus_parser import parse_upload

    pdf = Path(__file__).parent / "fixtures" / "aktu_ece_vii_viii_2026-27.pdf"
    if not pdf.exists():
        pytest.skip("AKTU fixture PDF not present")
    courses = parse_upload(pdf.name, pdf.read_bytes()).courses
    assert [c.code for c in courses] == [
        "BEC701", "BEC751", "BEC071", "BEC073", "BEC074", "BEC075"]
    assert all(c.title for c in courses), "a course lost its title"
    assert all(c.ltp for c in courses), "a course lost its L-T-P"


def test_a_recovered_title_lands_on_its_own_course():
    """
    The title sat between two "Course Outcomes:" headings, and the course
    boundary was only drawn later when the repeated CO1 was spotted — so the
    name was filed under the *previous* course. Two cards, both wrong.
    """
    text = (
        "BEC071 Wireless and Mobile Communication 3L:0T:0P 3 Credits\n" + OUTCOMES
        + "\nSome intervening prose that is not a course name at all.\n"
        + OUTCOMES.replace("wireless propagation and cellular concepts",
                           "microwave transmission lines and waveguides")
        + "\nBEC073 Radar and Navigational Aids 3L:0T:0P 3 Credits\n"
        + OUTCOMES.replace("wireless propagation and cellular concepts",
                           "radar range equations and detection theory")
    )
    courses = parse_text(text).courses
    assert len(courses) == 3
    assert (courses[0].code, courses[0].title) == (
        "BEC071", "Wireless and Mobile Communication")
    # The middle block has no header of its own and must stay blank...
    assert courses[1].title is None
    assert "microwave" in courses[1].cos[0].statement.lower()
    # ...and the third course's identity is its own, not the block before it.

    assert (courses[2].code, courses[2].title) == (
        "BEC073", "Radar and Navigational Aids")
    assert "radar" in courses[2].cos[0].statement.lower()


def test_a_second_outcomes_heading_starts_a_second_course():
    """Two headings, two courses — never one course with ten outcomes."""
    courses = parse_text(OUTCOMES + "\n" + OUTCOMES).courses
    assert len(courses) == 2
    assert all(len(c.cos) == 5 for c in courses)


# --------------------------------------------------- headers broken by layout

REFLOW_CASES = [
    # 1. the code split at its own hyphen
    ("BEC-\n502 MICROPROCESSOR & MICROCONTROLLER 3L:1T:0P 4 Credits",
     "BEC502", "MICROPROCESSOR & MICROCONTROLLER", (3, 1, 0)),
    # 2. the code split with no hyphen at all
    ("BAC\n052 REMOTE SENSING AND APPLICATIONS 3L:0T:0P 3 Credits",
     "BAC052", "REMOTE SENSING AND APPLICATIONS", (3, 0, 0)),
    # 3. the title wrapped onto the next line
    ("BAC-653A WIRELESS COMMUNICATIONS\nNETWORKS LAB 0L:0T:2P 1 Credit",
     "BAC653A", "WIRELESS COMMUNICATIONS NETWORKS LAB", (0, 0, 2)),
    # 4. the title wrapped across two further lines
    ("BEC-051 \nIoT - ARCHITECTURE, COMMUNICATION, \n"
     "TECHNOLOGY & ITS APPLICATIONS 3L:0T:0P 3 Credits",
     "BEC051", "IoT - ARCHITECTURE, COMMUNICATION, TECHNOLOGY & ITS APPLICATIONS",
     (3, 0, 0)),
]


@pytest.mark.parametrize("header,code,title,ltp", REFLOW_CASES)
def test_a_header_broken_by_the_page_layout_is_rejoined(header, code, title, ltp):
    """
    Sixteen of the twenty-six courses in the AKTU 2025-26 third-year scheme
    reached the UI with no code and no title because the PDF's layout split
    the banner. Each half matches no header shape on its own.
    """
    c = one(header)
    assert (c.code, c.title, c.ltp) == (code, title, ltp)


def test_the_reflow_is_anchored_on_the_ltp_marker():
    """
    The join only happens when the continuation actually completes an L-T-P.
    Without that anchor, any line starting with capitals would swallow the
    real header three lines below it.
    """
    text = ("ELECTRONICS AND COMMUNICATION ENGINEERING\n"
            "Curriculum & Evaluation Scheme (V & VI semester) Page 8\n"
            "BEC-501 INTEGRATED CIRCUITS 3L:1T:0P 4 Credits\n")
    out, joins = repair_pdf_text(text)
    assert joins >= 0
    assert "BEC-501 INTEGRATED CIRCUITS 3L:1T:0P 4 Credits" in out.splitlines()
    assert "ELECTRONICS AND COMMUNICATION ENGINEERING" in out


def test_ordinary_end_of_line_hyphenation_is_not_a_course_code():
    out, _ = repair_pdf_text("micro-\nprocessor architecture and its operations\n")
    assert out.splitlines()[0] == "micro-"


def test_the_third_year_scheme_parses():
    """
    The file that produced the screenshot full of "Untitled" cards. Sixteen of
    twenty-six courses had no identity; the reflow recovers all but three,
    and those three genuinely have no banner in the extracted text.
    """
    from pathlib import Path

    from app.engine.syllabus_parser import parse_upload

    pdf = (Path(__file__).parent / "fixtures"
           / "aktu_ece_v_vi_2025-26.pdf")
    if not pdf.exists():
        pytest.skip("third-year fixture not present")
    courses = parse_upload(pdf.name, pdf.read_bytes()).courses
    named = [c for c in courses if c.code]
    assert len(courses) >= 24
    assert len(named) >= 25, (
        f"only {len(named)} of {len(courses)} courses have a code: "
        + ", ".join(str(c.code) for c in courses))
    codes = {c.code for c in named}
    for expected in ("BEC501", "BEC502", "BEC503", "BAC052", "BEC601",
                     "BEC602", "BEC603", "BEC653B", "BEC653C"):
        assert expected in codes, f"{expected} lost its header"
    # and no course may borrow a reference line as its name
    for c in courses:
        if c.title:
            assert "Wiley" not in c.title and "ISBN" not in c.title


def test_a_unicode_hyphen_in_a_course_code_is_still_a_hyphen():
    """
    The AKTU 2025-26 fourth-year scheme writes four of its six course codes
    with U+2010 HYPHEN and two with the ASCII one. Only the ASCII two were
    recognised, so four of six courses reached the UI with no identity.
    """
    from app.engine.syllabus_parser import _normalise_hyphens

    for ch in ("\u2010", "\u2011", "\u2012", "\u2212", "\ufe63", "\uff0d"):
        assert _normalise_hyphens(f"BEC{ch}071 X") == "BEC-071 X"
    # An en dash is real punctuation in a title and must survive...
    assert _normalise_hyphens("IoT \u2013 Architecture") == "IoT \u2013 Architecture"
    assert _normalise_hyphens("5 \u2013 6 weeks") == "5 \u2013 6 weeks"
    # ...unless it is sitting inside a course code, where it is a hyphen.
    assert _normalise_hyphens("BEC\u2013071 X") == "BEC-071 X"


def test_the_fourth_year_2025_26_scheme_parses():
    """Every course in this one broke on the Unicode hyphen."""
    from pathlib import Path

    from app.engine.syllabus_parser import parse_upload

    pdf = Path(__file__).parent / "fixtures" / "aktu_ece_vii_viii_2025-26.pdf"
    if not pdf.exists():
        pytest.skip("fixture not present")
    courses = parse_upload(pdf.name, pdf.read_bytes()).courses
    assert len(courses) == 6
    assert all(c.code and c.title for c in courses), (
        "unnamed: " + ", ".join(str(c.code) for c in courses))
    assert {c.code for c in courses} == {
        "BEC701", "BEC751", "BEC071", "BEC072", "BEC073", "BEC074"}


def test_a_running_page_header_never_lands_inside_an_outcome():
    """
    "ELECTRONICS AND COMMUNICATION ENGINEERING" heads all thirty pages of a
    real scheme. It is a department name, not a page number, so the furniture
    patterns missed it and it was appended to whichever outcome was open when
    the page turned — and that text then fed the CO -> PO mapping.
    """
    page_break = "\n  ELECTRONICS AND COMMUNICATION ENGINEERING  \n"
    text = ""
    for n in range(4):                       # repeat so it reads as furniture
        text += (f"BEC70{n} Course Number {n} 3L:0T:0P 3 Credits\n"
                 + OUTCOMES.replace("wireless propagation and cellular concepts",
                                    f"topic number {n} and its uses")
                 + page_break)
    courses = parse_text(text).courses
    assert len(courses) == 4
    for c in courses:
        for co in c.cos:
            assert "ELECTRONICS AND COMMUNICATION" not in co.statement.upper(), (
                f"{c.code} {co.code} swallowed the page header: {co.statement}")


def test_repetition_alone_does_not_make_an_outcome_into_furniture():
    """
    Two courses in one scheme legitimately share an outcome — the third-year
    file has the same IoT statement under two electives. A numbered outcome is
    never counted as a running header however often it appears.
    """
    body = ("Course Outcomes: At the end of this course students will be able to:\n"
            "1. Introduction to IoTs enabled technologies.\n"
            "2. Analyze the performance of sensor networks.\n"
            "3. Apply IoT protocols to a connected device.\n")
    text = ""
    for n in range(4):
        text += f"BEC06{n} Elective Number {n} 3L:0T:0P 3 Credits\n" + body
    courses = parse_text(text).courses
    assert len(courses) == 4
    for c in courses:
        assert len(c.cos) == 3, f"{c.code} lost outcomes to the furniture filter"
        assert "IoTs enabled technologies" in c.cos[0].statement


def test_the_outcomes_of_the_real_schemes_carry_no_page_furniture():
    from pathlib import Path

    from app.engine.syllabus_parser import parse_upload

    for name in ("aktu_ece_vii_viii_2026-27.pdf", "aktu_ece_vii_viii_2025-26.pdf",
                 "aktu_ece_v_vi_2025-26.pdf"):
        pdf = Path(__file__).parent / "fixtures" / name
        if not pdf.exists():
            continue
        for course in parse_upload(pdf.name, pdf.read_bytes()).courses:
            for co in course.cos:
                upper = co.statement.upper()
                assert "COMMUNICATION ENGINEERING." not in upper, (
                    f"{name} {course.code} {co.code}: {co.statement}")
                assert "CURRICULUM & EVALUATION" not in upper
                assert "PAGE " not in upper
