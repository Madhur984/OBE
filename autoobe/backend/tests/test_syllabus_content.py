"""
The unit-wise syllabus, and what the scorer is allowed to do with it.

A course outcome is one sentence. The syllabus beneath it in the same
document is a page, and it is the only place that says what the course
actually teaches. Digital System Design's CO4 reads "Analyze various logic
families and design circuits using PLDs" -- nothing there evidences the
modern-tool-usage PO. Unit IV of the same course names "Verilog RTL coding"
and "Siemens EDA Precision RTL / Cadence Genus / Synopsys Design Compiler".

Reading it is worth doing and easy to overdo, so the rules the module states
are asserted here as arithmetic rather than left as intent:

  * course content can never map a PO on its own (SYLLABUS_CAP is below
    PARTIAL_THRESHOLD, and the guard needs the sentence to point there first)
  * omitting it reproduces the old behaviour exactly, which is what the
    accuracy evaluation measures
  * the terms it contributes are reported apart from the sentence's
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.competency import (
    PARTIAL_THRESHOLD, SYLLABUS_CAP, SYLLABUS_MIN_STATEMENT, propose_for_co,
)
from app.engine.mapping_rules import Competency
from app.engine.syllabus_parser import extract_courses_from_text, parse_upload

FIXTURES = Path(__file__).parent / "fixtures"

# BEC302, verbatim from the ECE 2nd-year scheme, in the shape PDF extraction
# actually produces it: the lecture count on its own line after each row.
DSD = """
BEC302 DIGITAL SYSTEM DESIGN 3L:1T:0P 4 Credits

Unit Topics Lectures
I Logic simplification and combinational logic design: Number Systems, Binary
arithmetic, review of Boolean algebra and Demorgans theorem, SOP & POS forms,
Karnaugh maps up to 5 variables, tabulation method.
8
II Combinational circuits: Analysis and Design of combinational circuits, MSI
devices like comparators, multiplexers, demultiplexers, encoder, decoder.
8
IV Logic families and semiconductor memories: TTL NAND gate, specifications,
noise margin, concept of programmable logic devices such as FPGA, Verilog RTL
coding of combinational and sequential elements, and logic implementation
using programmable devices (Siemens EDA Precision RTL / Cadence Genus /
Synopsys Design Compiler).
8

Text/Reference Books:
1. R.P. Jain, "Modern Digital Electronics," Tata McGraw Hill, 4th edition, 2009.

Course outcomes:
At the end of this course students will demonstrate the ability to:
1. Perform numerous arithmetic and logic simplification using various methods.
2. Design and analyze modular combinational circuits with MUX / DEMUX.
"""


# BEC301 Electronic Devices, units I-V verbatim, as the parser reads them out
# of the ECE 2nd-year scheme. This is the whole course's content, which is
# what the page passes: the block describes the course, not one outcome.
UNIT_TEXT = (
    "Introduction to semiconductor physics: Review of quantum mechanics, "
    "electrons in periodic lattices, E-k diagrams, Effective Mass. "
    "Energy bands in intrinsic and extrinsic silicon, carrier transport, "
    "diffusion current, drift current, mobility and resistivity, sheet "
    "resistance, Generation and recombination of carriers, Poisson and "
    "continuity equation. "
    "P-N junction characteristics, I-V characteristics, and small signal "
    "switching models, Avalanche breakdown, Zener diode, Schottky diode. "
    "Bipolar Junction Transistor, various configurations and their features "
    "I-V characteristics, DC biasing schemes for BJT, Ebers-Moll model. "
    "Field Effect Transistor, DC biasing schemes, MOSFET, I-V "
    "characteristics, MOS capacitor, C-V characteristics."
)


@pytest.fixture()
def client():
    return TestClient(app)


# ------------------------------------------------------ reading the table

def test_the_unit_table_is_read_and_the_outcomes_still_are():
    course = extract_courses_from_text(DSD)[0]
    assert course.code == "BEC302"
    assert [u.number for u in course.units] == ["I", "II", "IV"]
    assert len(course.cos) == 2          # the CO list is untouched by all this
    assert course.cos[0].statement.startswith("Perform numerous arithmetic")


def test_the_lecture_count_is_not_part_of_the_topics():
    course = extract_courses_from_text(DSD)[0]
    assert not any(u.topics.endswith("8") for u in course.units)
    assert "Karnaugh maps" in course.units[0].topics


def test_the_table_ends_where_the_document_ends_it():
    """Reference books and the CO list must not leak into the course content."""
    text = extract_courses_from_text(DSD)[0].syllabus_text
    assert "Tata McGraw Hill" not in text
    assert "Perform numerous arithmetic" not in text
    assert "Verilog" in text and "Precision RTL" in text


def test_the_unit_lead_in_is_its_title():
    units = extract_courses_from_text(DSD)[0].units
    assert units[0].title == "Logic simplification and combinational logic design"
    assert units[1].title == "Combinational circuits"


def test_a_lab_has_no_unit_table_and_that_is_not_an_error():
    """
    A lab course lists experiments, not units. It must come back with no
    content rather than with the previous course's.
    """
    parsed = parse_upload("x.pdf", (FIXTURES / "aktu_ece_vii_viii_2025-26.pdf").read_bytes())
    labs = [c for c in parsed.courses if c.title and "Lab" in c.title]
    assert labs, "the fixture is expected to contain a lab course"
    for lab in labs:
        assert lab.units == []
        assert lab.cos, "its outcomes are still read"


def test_the_real_pdf_yields_a_unit_table_per_theory_course():
    parsed = parse_upload("x.pdf", (FIXTURES / "aktu_ece_v_vi_2025-26.pdf").read_bytes())
    with_units = [c for c in parsed.courses if c.units]
    assert len(with_units) >= 15
    # five units is the AKTU norm, and the content is substantial
    assert all(3 <= len(c.units) <= 6 for c in with_units)
    assert all(len(c.syllabus_text) > 500 for c in with_units)


# ------------------------------------------- what the scorer may do with it

def _comp(statement, syllabus="", blooms=(4,)):
    return {p.po_code: p.competency for p in propose_for_co(
        statement, framework_code="GAPC_V3", blooms_levels=list(blooms),
        syllabus=syllabus)}


def test_omitting_the_syllabus_reproduces_the_old_behaviour():
    """
    The accuracy evaluation runs without course content. If passing none is
    not identical to the previous code path, its floors stop meaning anything.
    """
    for s in ("Design various ADCs and DACs according to the given specifications.",
              "Explain the working of a transistor.",
              "Work effectively in a team to deliver a project report."):
        a = propose_for_co(s, framework_code="GAPC_V3", blooms_levels=[4])
        b = propose_for_co(s, framework_code="GAPC_V3", blooms_levels=[4], syllabus="")
        assert [(p.po_code, p.competency, p.score) for p in a] == \
               [(p.po_code, p.competency, p.score) for p in b]
        assert all(not p.syllabus_terms for p in a)


def test_course_content_alone_can_map_nothing():
    """
    Arithmetic, not policy: the cap is below the partial threshold, so no
    quantity of syllabus text reaches a mapping by itself.
    """
    assert SYLLABUS_CAP < PARTIAL_THRESHOLD
    # a syllabus dense in tool, ethics and teamwork terms against an outcome
    # that names none of them
    syllabus = ("MATLAB simulation, SPICE, CAD tools, EDA, Verilog, VHDL. "
                "Professional ethics, code of conduct, plagiarism, integrity. "
                "Team work, collaboration, group project, leader.")
    got = _comp("Recall the standard colour code of a resistor.", syllabus)
    for po in ("PO5", "PO8", "PO9"):
        assert po not in got, f"{po} was mapped from course content alone"


def test_the_outcome_must_point_at_the_po_before_content_counts():
    """
    A course that teaches simulation does not make every one of its outcomes
    a tool-usage outcome. Below SYLLABUS_MIN_STATEMENT the content is not
    consulted at all, and the evidence says so by being empty.
    """
    syllabus = "MATLAB simulation and SPICE modelling of analog circuits."
    weak = [p for p in propose_for_co("Recall the colour code of a resistor.",
                                      framework_code="GAPC_V3", blooms_levels=[1],
                                      syllabus=syllabus) if p.po_code == "PO5"]
    assert weak == [] or weak[0].syllabus_terms == []
    assert SYLLABUS_MIN_STATEMENT > 0


def test_content_corroborates_an_outcome_that_already_points_there():
    """
    The case this exists for. "Realize the mathematical models of MOS
    transistors" reads as a partial engineering-knowledge match on its own;
    the unit behind it is five lines of semiconductor physics and carrier
    transport, and that is what a reviewer would look at next.
    """
    statement = "Realize the mathematical models of MOS transistors."
    alone = _comp(statement)
    with_content = _comp(statement, UNIT_TEXT)
    assert alone["PO1"] is Competency.PARTIAL
    assert with_content["PO1"] is Competency.STRONG


def test_the_two_kinds_of_evidence_are_reported_apart():
    statement = "Realize the mathematical models of MOS transistors."
    p = [x for x in propose_for_co(
        statement, framework_code="GAPC_V3", blooms_levels=[4],
        syllabus=UNIT_TEXT)
        if x.po_code == "PO1"][0]
    assert p.syllabus_terms and p.matched_terms
    assert not any(t.startswith("verb ") for t in p.syllabus_terms)
    assert "Corroborated by the course syllabus" in p.evidence
    assert 0 < p.syllabus_score <= SYLLABUS_CAP


# ------------------------------------------------------------------- API

def test_the_parse_endpoint_returns_the_course_content(client):
    data = (FIXTURES / "aktu_ece_vii_viii_2025-26.pdf").read_bytes()
    body = client.post("/api/parse-syllabus",
                       files={"file": ("s.pdf", data, "application/pdf")}).json()
    vlsi = next(c for c in body["courses"] if (c["code"] or "").endswith("701"))
    assert len(vlsi["units"]) == 5
    assert vlsi["units"][0]["number"] == "I"
    assert vlsi["syllabus"].startswith(vlsi["units"][0]["topics"][:20])


def test_the_propose_endpoint_takes_the_syllabus_and_says_it_did(client):
    payload = {
        "framework_code": "GAPC_V3",
        "cos": {"CO1": {"statement": "Realize the mathematical models of MOS transistors.",
                        "blooms_levels": [4]}},
    }
    without = client.post("/api/propose-mapping", json=payload).json()
    with_ = client.post("/api/propose-mapping", json={
        **payload,
        "syllabus": UNIT_TEXT,
    }).json()
    assert without["summary"]["syllabus_chars"] == 0
    assert with_["summary"]["syllabus_chars"] > 0
    assert without["competency"]["CO1"]["PO1"] == 1
    assert with_["competency"]["CO1"]["PO1"] == 2
    ev = next(e for e in with_["evidence"]["CO1"] if e["po_code"] == "PO1")
    assert ev["syllabus_terms"] and ev["syllabus_score"] > 0


# ------------------------------- a lab's syllabus is its experiment list

LAB = """
BEC351 ELECTRONIC DEVICES LAB 0L:0T:2P 1 Credits
SUGGESTIVE LIST OF EXPERIMENTS
1. Study of Lab Equipment and Components: CRO, multimeter, and function
generator, power supply- active, passive components and bread board.
2. P-N Junction diode: Characteristics of PN junction diode - static and
dynamic resistance measurement from graph.
3. Applications of PN Junction diode: Half & Full wave rectifier -
Measurement of Vrms, Vdc, and ripple factor.
Course Outcomes:
CO1 Understand working of basic electronics lab equipment. K2
CO2 Understand working of PN junction diode and its applications. K2
"""


def test_a_lab_carries_its_experiment_list_as_course_content():
    """
    A lab has no unit table, so before this every laboratory course in the
    corpus reached the scorer with no course content at all. That matters
    more for a lab than for a lecture course: the two POs a lab most
    obviously serves are Conduct Investigations and Modern Tool Usage, and
    neither is evidenced by "Understand working of PN junction diode".
    """
    course = extract_courses_from_text(LAB)[0]
    assert course.code == "BEC351"
    assert not course.units
    assert "CRO" in course.experiments and "multimeter" in course.experiments
    assert course.experiments in course.syllabus_text


def test_the_experiment_list_is_never_read_as_outcomes():
    """
    "Write a program to demonstrate the working of the ID3 algorithm" is a
    task a student performs in one session, not a statement of what the
    course achieves. The list ends where the CO list begins.
    """
    course = extract_courses_from_text(LAB)[0]
    assert [c.code for c in course.cos] == ["CO1", "CO2"]
    assert all("Study of Lab Equipment" not in c.statement for c in course.cos)
    assert "Course Outcomes" not in course.experiments
    assert "Understand working" not in course.experiments


def test_the_experiment_list_evidences_the_columns_a_lab_earns():
    """Corroboration, and only that -- it is capped below the partial
    threshold like every other piece of course content."""
    from app.engine.competency import signal_strength

    course = extract_courses_from_text(LAB)[0]
    statement = course.cos[1].statement          # the PN junction one
    alone = signal_strength(statement, framework_code="GAPC_V3")
    with_lab = signal_strength(statement, framework_code="GAPC_V3",
                               syllabus=course.syllabus_text)
    assert not alone, "this outcome evidences nothing on its own"
    assert with_lab.get("PO5", 0) > 0, "the CRO and the multimeter went unread"
    assert max(with_lab.values()) <= SYLLABUS_CAP


def test_every_lab_in_a_real_semester_file_now_has_content():
    parsed = parse_upload("x.pdf", (FIXTURES / "aktu_ece_v_vi_2025-26.pdf").read_bytes())
    labs = [c for c in parsed.courses if "LAB" in (c.title or "").upper()]
    assert len(labs) >= 6
    assert all(c.experiments for c in labs), \
        [c.code for c in labs if not c.experiments]
    # and the theory courses are untouched: they still have unit tables and
    # no experiment list
    theory = [c for c in parsed.courses
              if c.units and "LAB" not in (c.title or "").upper()]
    assert len(theory) >= 10
    assert not any(c.experiments for c in theory)
