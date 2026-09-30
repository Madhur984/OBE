"""
The bundled AKTU catalogue, and the matching that finds a course in it.

The department was told, after the Universal Human Values case, that the fix
generalised but the inputs did not: five more of their non-technical blocks
would stay thin until somebody found and uploaded five more syllabus PDFs.
Asking them to do that is asking them to do the university's filing twice.

So AKTU's own syllabi ship with the tool. What is tested here is not the
syllabi -- those are the university's -- but the two things that can go
wrong with them:

  * **matching the wrong course.** "Cyber Security" sits inside AKTU's
    "Introduction to Security of Cyber-Physical Systems", and a containment
    match handed a department that course's syllabus. A lab and its theory
    course share every word of their titles. Both are pinned below.
  * **speaking for a department.** A catalogue outcome must never replace the
    outcome a department wrote. They rewrite AKTU's verbs deliberately.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.aktu_catalogue import (
    code_key, find_course, load_catalogue, normalise_title, score, syllabus_for,
)


@pytest.fixture(scope="module")
def catalogue():
    cat = load_catalogue()
    if not cat:
        pytest.skip("app/data/aktu_catalogue.json not built")
    return cat


@pytest.fixture()
def client():
    return TestClient(app)


# ------------------------------------------------------------ the artefact

def test_the_catalogue_is_substantial_and_carries_syllabus_content(catalogue):
    assert len(catalogue) >= 300, f"catalogue shrank to {len(catalogue)} courses"
    with_syllabus = [c for c in catalogue if c.syllabus]
    assert len(with_syllabus) >= 150
    assert all(c.code and c.title for c in catalogue)
    # Two outcomes, or a real syllabus. Either is enough: this catalogue
    # exists for the syllabus, and AKTU publishes some courses -- the
    # non-credit papers among them -- with five modules of content and a
    # single outcome written as running prose rather than a list.
    assert all(len(c.cos) >= 2 or len(c.syllabus) >= 200 for c in catalogue)


def test_no_entry_is_a_bibliography(catalogue):
    """
    One AKTU document yields a "course" whose outcomes are its reference list
    -- "V. Sivaramakrishna (Ed.), Cultural Heritage of India, Bharatiya Vidya
    Bhavan, Mumbai". A bibliography scored against Programme Outcomes is
    nonsense that looks like data, so the builder drops them.
    """
    bad = [(c.code, s) for c in catalogue for s in c.cos
           if any(w in s.lower() for w in
                  ("(ed.)", "bhavan", "prentice", "mcgraw", "isbn", "new delhi"))]
    assert not bad, f"reference entries reached the catalogue: {bad[:3]}"


def test_every_entry_names_the_document_it_came_from(catalogue):
    """Provenance is not decoration: a 2021 syllabus standing in for a 2026
    course is a judgement the department has to be able to see and refuse."""
    assert all(c.source.endswith(".pdf") for c in catalogue)
    assert all(c.year.startswith("20") for c in catalogue)


# -------------------------------------------------------------- identifying

@pytest.mark.parametrize("text,key", [
    ("KVE-301", "VE301"), ("BVE301", "VE301"),          # a code revision
    ("BEC-302", "EC302"), ("KEC302", "EC302"),
    ("BEC653B", "EC653B"),                              # branch suffix kept
    ("KHU-801", "HU801"),
    ("MATHEMATICS-III", ""),                            # no code at all
])
def test_a_code_survives_the_series_letter_changing(text, key):
    assert code_key(text) == key


def test_an_acronym_finds_its_expansion(catalogue):
    """
    A department writes UHVPE; AKTU prints "Universal Human Values and
    Professional Ethics". The two share no word, so no amount of fuzzy
    matching finds it -- and this is the course whose syllabus mattered most.
    """
    assert "universal" in normalise_title("UHVPE")
    found, s = find_course(title="UHVPE", code="KVE-301", catalogue=catalogue)
    assert found is not None and found.code == "BVE301"
    assert found.syllabus and "co-existence" in found.syllabus.lower()


def test_a_lab_never_stands_in_for_its_theory_course(catalogue):
    """
    "Digital System Design" and "Digital System Design Lab" share every word.
    Their outcomes and their content are different, and a lab has no unit
    table at all -- so a wrong match here silently maps a lab off a theory
    syllabus.
    """
    theory, _ = find_course(title="Digital System Design", code="BEC-302",
                            catalogue=catalogue)
    lab, _ = find_course(title="Digital System Design Lab", code="BEC352",
                         catalogue=catalogue)
    assert theory is not None and lab is not None
    assert theory.code != lab.code
    assert "LAB" in lab.title.upper() and "LAB" not in theory.title.upper()


def test_a_short_title_does_not_match_a_longer_different_course(catalogue):
    """
    The regression that forced the two-sided rule. Containment scored
    "Cyber Security" a perfect match against "Introduction to Security of
    Cyber-Physical Systems" -- every word present, entirely the wrong course
    -- and handed the department that course's syllabus.
    """
    victim = next((c for c in catalogue
                   if "cyber-physical" in c.title.lower()), None)
    if victim is None:
        pytest.skip("the colliding course is not in this build of the catalogue")
    assert score("Cyber Security", "BCC-401", victim) < 0.7


def test_a_fuller_university_title_still_matches(catalogue):
    """
    The case containment was introduced for, now carried by the code instead:
    the department writes "Rural Development", AKTU prints "Rural Development:
    Administration and Planning".
    """
    found, s = find_course(title="Rural Development", code="KHU-801",
                           catalogue=catalogue)
    assert found is not None and found.code.upper().endswith("801")
    assert s >= 0.7


def test_nothing_is_returned_rather_than_a_bad_guess(catalogue):
    found, s = find_course(title="Quantum Basket Weaving", code="ZZZ-999",
                           catalogue=catalogue)
    assert found is None


# ------------------------------------------------------- what it may supply

def test_it_supplies_syllabus_and_provenance_never_outcomes():
    text, source = syllabus_for(title="UHVPE", code="KVE-301")
    assert text and "harmony" in text.lower()
    assert source["code"] == "BVE301"
    assert source["source"].endswith(".pdf") and source["year"]
    assert "cos" not in source, "a department's outcomes are their own"


def test_the_api_reports_the_stand_in_rather_than_using_it_silently(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [2]}, "competency": {},
        "co_statements": {"CO1": "Understand the harmony in the family and society."},
        "course_title": "UHVPE", "course_code": "KVE-301",
        "core_convention": True, "core_require_evidence": True,
        "lifelong_convention": True,
    }).json()
    src = body["syllabus_source"]
    assert src["code"] == "BVE301" and src["units"] == 5
    assert body["syllabus_chars"] > 500
    assert "PO8" in body["levels"]["CO1"], "the stand-in syllabus did not reach the scorer"


def test_an_uploaded_syllabus_always_wins(client):
    """The catalogue is a fallback, never an override."""
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [2]}, "competency": {},
        "co_statements": {"CO1": "Understand the harmony in the family and society."},
        "syllabus": "Unit I: the department's own text.",
        "course_title": "UHVPE", "course_code": "KVE-301",
        "core_convention": True, "core_require_evidence": True,
    }).json()
    assert body["syllabus_source"] == {}
    assert body["syllabus_chars"] == len("Unit I: the department's own text.")


def test_it_can_be_turned_off(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [2]}, "competency": {},
        "co_statements": {"CO1": "Understand the harmony in the family and society."},
        "course_title": "UHVPE", "course_code": "KVE-301", "use_catalogue": False,
        "core_convention": True, "core_require_evidence": True,
    }).json()
    assert body["syllabus_source"] == {} and body["syllabus_chars"] == 0


def test_the_lookup_endpoint_offers_the_runners_up(client):
    body = client.get("/api/catalogue",
                      params={"title": "Digital Signal Processing",
                              "code": "BEC503"}).json()
    assert body["courses"] >= 300
    assert body["match"]["code"].upper().endswith("503")
    assert len(body["near"]) >= 2
    assert body["near"][0]["score"] >= body["near"][1]["score"]


def test_the_lookup_endpoint_is_quiet_when_asked_nothing(client):
    body = client.get("/api/catalogue").json()
    assert body["match"] is None and body["near"] == []
