"""
The non-technical half of the framework, and the course that exposed it.

The department uploaded `AKTU_UHVPE.pdf` -- Universal Human Values and
Professional Ethics -- and got **zero** against PO8 Ethics on every outcome:
"its uhvpe syllabus it is based on human values still got 0 mapping under
ethics ... thats why analysation is most important."

Three separate defects produced that, and each is pinned below:

  1. **The last outcome of a numbered list was dropped.** Nothing follows the
     final item to stop it, so it ran on into "Catalogue Description" and was
     then discarded for exceeding MAX_STATEMENT_CHARS. The outcome lost was
     CO5, "Distinguish between ethical and unethical practices" -- the only
     one in the whole course containing the word "ethical".
  2. **Its syllabus was not read at all.** This document writes units as a
     heading followed by prose ("UNIT-5 Implications ... on Professional
     Ethics"), not as the "Unit Topics Lectures" table the engineering
     syllabi use, so five units of human values were invisible.
  3. **No lexicon held any of its vocabulary.** Value education, natural
     acceptance, harmony, co-existence, human-human relationship,
     humanistic -- an ethics lexicon drawn only from the PO statements has
     none of them, and neither does a teamwork lexicon that knows "team".

Ground truth throughout is the department's own sheet, which maps this course
PO6 / PO7 / PO8 / PO9 on every outcome and leaves PO1-PO5 empty.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.competency import propose_for_co, signal_strength
from app.engine.conventions import NON_TECHNICAL, classify_course, columns_for_course
from app.engine.syllabus_parser import parse_upload

FIXTURES = Path(__file__).parent / "fixtures"
TITLE = "Universal Human Values and Professional Ethics"


@pytest.fixture(scope="module")
def uhv():
    return parse_upload("uhv.pdf", (FIXTURES / "aktu_uhvpe.pdf").read_bytes()).courses[0]


@pytest.fixture()
def client():
    return TestClient(app)


# ------------------------------------------------------------- the parser

def test_the_last_outcome_of_the_list_survives(uhv):
    """
    Five outcomes, not four. The fifth is the ethics one, and losing it took
    the ethics column of an ethics course with it.
    """
    assert len(uhv.cos) == 5
    assert "ethical and unethical" in uhv.cos[4].statement
    # and it stopped at the end of its own sentence rather than swallowing
    # the section heading that follows it
    assert "Catalogue" not in uhv.cos[4].statement
    assert len(uhv.cos[4].statement) < 200


def test_a_finished_sentence_is_not_continued(uhv):
    for co in uhv.cos:
        assert co.statement.rstrip().endswith((".", "y", "n", "e", "s")), co.statement[-40:]
        assert len(co.statement) <= 400


def test_units_written_as_headings_are_read_too(uhv):
    """
    "UNIT-1 Introduction to Value Education" then prose -- no table, no
    lecture-count column. Five units, and the fifth is the one that matters.
    """
    assert len(uhv.units) == 5
    assert [u.number for u in uhv.units] == ["1", "2", "3", "4", "5"]
    text = uhv.syllabus_text.lower()
    assert "professional ethics" in text
    assert "value education" in text
    assert "co-existence" in text


def test_the_engineering_table_layout_still_parses():
    """The heading form must not have displaced the tabular one."""
    ece = parse_upload("x.pdf", (FIXTURES / "aktu_ece_v_vi_2025-26.pdf").read_bytes())
    with_units = [c for c in ece.courses if c.units]
    assert len(with_units) >= 15
    assert all(3 <= len(c.units) <= 6 for c in with_units)


# ------------------------------------------------------------ the lexicon

def test_a_human_values_course_evidences_ethics_on_every_outcome(uhv):
    """The department's complaint, as an assertion."""
    for co in uhv.cos:
        ev = signal_strength(co.statement, framework_code="GAPC_V3",
                             syllabus=uhv.syllabus_text)
        assert ev.get("PO8", 0) > 0, f"{co.code} still scores 0 against Ethics"


def test_it_evidences_team_work_too(uhv):
    """
    PO9 is "Function effectively as an individual, and as a member ... in
    diverse teams". A course about human-human relationships, family and
    society is that, and the department maps it there on every row -- but it
    never uses the word "team".
    """
    for co in uhv.cos:
        ev = signal_strength(co.statement, framework_code="GAPC_V3",
                             syllabus=uhv.syllabus_text)
        assert ev.get("PO9", 0) > 0, f"{co.code} scores 0 against Individual and Team Work"


def test_it_claims_none_of_the_technical_columns(uhv):
    """
    The other half of "analysation": a course on the Self and the Body must
    not propose Engineering Knowledge. One incidental "basic" used to do it.
    """
    for co in uhv.cos:
        proposed = {p.po_code for p in propose_for_co(
            co.statement, framework_code="GAPC_V3", blooms_levels=[2],
            syllabus=uhv.syllabus_text)}
        assert not (proposed & {"PO1", "PO2", "PO3", "PO4", "PO5"}), \
            f"{co.code} proposed {proposed & {'PO1', 'PO2', 'PO3', 'PO4', 'PO5'}}"


def test_harmonic_is_not_harmony():
    """
    "harmon" as a stem would have matched "harmonic distortion", which is in
    every analog syllabus and means nothing like harmony. The two live in the
    lexicon separately for this reason.
    """
    ev = signal_strength("Analyze harmonic distortion in a power amplifier.",
                         framework_code="GAPC_V3")
    assert ev.get("PO8", 0) == 0
    assert signal_strength("Understand the harmony in nature and existence.",
                           framework_code="GAPC_V3").get("PO8", 0) > 0


def test_a_relationship_between_quantities_is_not_team_work():
    """Bare "relationship" is deliberately not in the teamwork lexicon."""
    ev = signal_strength("Derive the relationship between voltage and current "
                         "in an RLC circuit.", framework_code="GAPC_V3")
    assert ev.get("PO9", 0) == 0


# ------------------------------------------------------------ end to end

def test_the_course_maps_the_half_the_department_maps_it_on(uhv, client):
    """
    Their sheet: PO6, PO7, PO8, PO9 on every outcome, PO1-PO5 empty
    throughout. This asserts the shape, not their exact levels -- the levels
    differ because they rewrote the syllabus verbs (their C202.3 reads
    "Analyse the value of harmonious relationship" where AKTU publishes
    "Understand the value of ..."), which is a course-outcome quality
    question and not a mapping one.
    """
    kind, cols = columns_for_course(title=TITLE, code=uhv.code, ltp=uhv.ltp)
    assert kind == NON_TECHNICAL
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {c.code: (c.blooms_levels or [2]) for c in uhv.cos},
        "competency": {},
        "co_statements": {c.code: c.statement for c in uhv.cos},
        "syllabus": uhv.syllabus_text,
        "course_title": TITLE, "course_code": uhv.code,
        "core_convention": True, "core_require_evidence": True,
        "core_on_conflict": "raise", "lifelong_convention": True,
    }).json()
    assert body["course_kind"] == NON_TECHNICAL
    for co in uhv.cos:
        mapped = body["levels"][co.code]
        for po in ("PO6", "PO7", "PO8", "PO9"):
            assert po in mapped, f"{co.code} has no {po}"
        for po in ("PO1", "PO2", "PO3", "PO4", "PO5"):
            assert po not in mapped, f"{co.code} claims {po} on a values course"


def test_the_title_alone_classifies_it_before_anything_is_read():
    assert classify_course(title=TITLE) == NON_TECHNICAL
    assert classify_course(title="UHVPE", code="KVE-301") == NON_TECHNICAL
    assert classify_course(title="Universal Human Value and Professional Ethics",
                           code="BVE301") == NON_TECHNICAL


def test_the_title_printed_above_the_code_is_recovered(uhv):
    """
    This document has no line a course header can match — the code line is
    "BVE301 / BVE401/  L T P C", a run of codes and column letters — so the
    name is only recoverable by position. Without it the course reached the
    workbook as "Untitled (BVE301)".
    """
    assert uhv.title == "Universal Human Values and Professional Ethics"
    assert uhv.code == "BVE301"


def test_a_constitution_course_does_not_claim_problem_analysis(client):
    """
    The scorer reads one sentence at a time and has no idea what course it
    belongs to. "Differentiate and relate the functioning of Indian
    parliamentary system at the centre and state level" contains
    "differentiate", which is a Problem Analysis verb wherever it appears, so
    the sentence proposes PO2 at competency 2 -- on a course about the
    Constitution of India.

    Their sheet maps PO1-PO5 on zero of the 25 outcomes across their six
    non-technical courses. The course kind wins over the sentence, and the
    cells it takes back are reported rather than silently dropped.
    """
    statement = ("Differentiate and relate the functioning of Indian "
                 "parliamentary system at the centre and state level.")
    proposed = {p.po_code for p in propose_for_co(
        statement, framework_code="GAPC_V3", blooms_levels=[4])}
    assert "PO2" in proposed, "the premise changed: the verb no longer reads that way"

    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO2": [4]}, "competency": {"CO2": {"PO2": 2}},
        "co_statements": {"CO2": statement},
        "course_title": "Constitution of India, Law and Engineering",
        "course_code": "BNC-501",
        "core_convention": True, "core_require_evidence": True,
        "core_unevidenced": "partial", "lifelong_convention": True,
    }).json()
    assert body["course_kind"] == NON_TECHNICAL
    assert body["withheld_cells"] == [{"co_code": "CO2", "po_code": "PO2"}]
    assert not (set(body["levels"]["CO2"]) & {"PO1", "PO2", "PO3", "PO4", "PO5"})
    assert "PO6" in body["levels"]["CO2"]


def test_a_technical_course_keeps_everything_it_earns(client):
    """
    The reverse is deliberately not done. An engineering course is left free
    to reach PO6-PO11 where its text earns it -- their sheet's 0% there is a
    fact about ECE syllabi, not a rule about engineering.
    """
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [4]},
        "competency": {"CO1": {"PO2": 2, "PO6": 2}},
        "co_statements": {"CO1": "Analyse the environmental impact of a power plant."},
        "course_title": "Power Plant Engineering", "course_code": "BME601",
        "core_convention": True, "core_require_evidence": True,
    }).json()
    assert body["withheld_cells"] == []
    assert "PO2" in body["levels"]["CO1"] and "PO6" in body["levels"]["CO1"]
