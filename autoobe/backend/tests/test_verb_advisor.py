"""
Replacing a vague verb, without moving the numbers by accident.

The client's guidelines name six verbs to avoid and thirteen to prefer. The
substitution looks trivial and is not: the verb sets the CO's Bloom level,
Bloom sets CL, and under Rule 2 a strong match with CL < PL maps at CL. Swap
"Understand" (K2) for "Design" (K6) and every strong cell in that row moves
from 1 to 3. So these tests cover two things equally — that the rewrite reads
like English, and that the level consequence is reported before it happens.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.blooms import VAGUE_VERBS
from app.engine.co_quality import GOOD_VERBS
from app.engine.verb_advisor import (
    GUIDELINE_VERBS, apply_verb, buried_verb, detect_issue,
    detect_vague_opening, detect_verbless_opening, level_change,
    nominal_verb, offline_recommendation, suggest_verbs,
)


@pytest.fixture()
def client():
    return TestClient(app)


def test_the_guideline_lists_are_the_ones_the_client_supplied():
    assert GUIDELINE_VERBS == (
        "define", "identify", "explain", "apply", "analyze", "design",
        "evaluate", "develop", "implement", "demonstrate", "solve", "compare",
        "differentiate")
    for verb in GUIDELINE_VERBS:
        assert verb in GOOD_VERBS, f"{verb} has no Bloom's level"
    for vague in ("know", "understand", "learn", "gain knowledge",
                  "be familiar with", "appreciate"):
        assert vague in VAGUE_VERBS


@pytest.mark.parametrize("statement,expected", [
    ("Understand the fundamentals of IoT.", "understand"),
    ("Know the design of a filter.", "know"),
    ("Learn to solve numerical problems.", "learn"),
    ("Gain knowledge of optical fibres.", "gain knowledge"),
    ("Be familiar with the performance of GSM.", "be familiar with"),
    ("Appreciate the role of ethics in practice.", "appreciate"),
    ("To understand the basics of control systems.", "understand"),
])
def test_vague_openings_are_detected(statement, expected):
    assert detect_vague_opening(statement) == expected


def test_a_working_verb_is_left_alone():
    """
    Offering to change a verb that is already measurable is how silent level
    drift starts. Nothing is suggested when nothing is wrong.
    """
    assert detect_vague_opening("Explain the evolution of wireless systems.") is None
    assert suggest_verbs("Analyze the stability of a feedback system.") == []


@pytest.mark.parametrize("statement,verb,expected", [
    # the preposition the vague phrase carried goes with it
    ("Gain knowledge of optical fibres.", "explain", "Explain optical fibres."),
    ("Be familiar with the performance of GSM.", "analyze",
     "Analyze the performance of GSM."),
    ("Understand about the basics of radar.", "define", "Define the basics of radar."),
    # "how" is an object, not a preposition, and has to survive
    ("Understand how to represent drone systems.", "explain",
     "Explain how to represent drone systems."),
    # a verb hiding behind the vague one is used as-is, not prefixed
    ("Learn to solve numerical problems.", "solve", "Solve numerical problems."),
    # a missing full stop is added
    ("Know the design of a filter", "identify", "Identify the design of a filter."),
])
def test_the_rewrite_reads_like_english(statement, verb, expected):
    assert apply_verb(statement, verb) == expected


def test_a_verb_hidden_behind_the_vague_one_is_found():
    assert buried_verb("Learn to solve numerical problems.") == "solve"
    assert buried_verb("Know to design a microwave circuit.") == "design"
    assert buried_verb("Understand the fundamentals of IoT.") is None


def test_the_hidden_verb_wins_over_a_guess():
    """It is the author's own word; no ranking heuristic beats that."""
    top = suggest_verbs("Learn to solve numerical problems on transmission lines.",
                        blooms_levels=[3])[0]
    assert top.verb == "solve"


def test_the_declared_bloom_level_is_preferred():
    """
    A replacement at the declared level moves no mapping cell. One at a
    different level moves the whole row, so it must not be offered first
    without reason.
    """
    top = suggest_verbs("Understand the fundamentals of IoT.", blooms_levels=[2])[0]
    assert top.verb == "explain"
    assert GOOD_VERBS[top.verb] == 2
    assert top.keeps_declared_level is True


def test_the_sentence_decides_when_no_level_is_declared():
    by_verb = {s.verb: s for s in suggest_verbs(
        "Gain knowledge of the difference between conventional and microwave tubes.")}
    assert "compare" in by_verb or "differentiate" in by_verb
    top = suggest_verbs(
        "Gain knowledge of the difference between conventional and microwave tubes.")[0]
    assert top.verb in ("compare", "differentiate")


def test_a_verb_that_repeats_the_next_word_is_not_offered_first():
    """"Design the design requirements" reads like a machine wrote it."""
    top = suggest_verbs("Know the design requirements of a microwave circuit.")[0]
    assert top.verb != "design"
    assert not apply_verb("Know the design requirements of a microwave circuit.",
                          top.verb).lower().startswith("design the design")


def test_every_suggestion_reports_its_level_consequence():
    for s in suggest_verbs("Understand the fundamentals of IoT.", blooms_levels=[2]):
        change = level_change("Understand the fundamentals of IoT.", s.verb,
                              blooms_levels=[2])
        assert change["new_bloom"] == s.bloom_level
        assert change["old_action_level"] == 1
        # the flag has to agree with the numbers beside it
        assert change["moves_action_level"] == (
            change["new_action_level"] != change["old_action_level"])


def test_swapping_understand_for_design_is_flagged_as_moving_the_row():
    """The exact failure this module guards: K2 -> K6 turns 1s into 3s."""
    change = level_change("Understand the fundamentals of IoT.", "design",
                          blooms_levels=[2])
    assert (change["old_action_level"], change["new_action_level"]) == (1, 3)
    assert change["moves_action_level"] is True


def test_a_verb_outside_the_guidelines_is_refused():
    with pytest.raises(ValueError, match="guideline verbs"):
        level_change("Understand the basics.", "grok")


# --------------------------------------------------- outcomes with no verb

# Verbatim from the three published schemes in tests/fixtures.
VERBLESS = [
    ("Application of IoT in Industrial and Commercial Building Automation.",
     "Application of", "apply", "Apply IoT in Industrial and Commercial Building Automation."),
    ("Designing of logic gates.",
     "Designing of", "design", "Design logic gates."),
    ("Implementation of combinational and sequential circuits using CMOS logic.",
     "Implementation of", "implement",
     "Implement combinational and sequential circuits using CMOS logic."),
    ("Introduction to IoTs enabled technologies.",
     "Introduction to", "explain", "Explain IoTs enabled technologies."),
]


@pytest.mark.parametrize("statement,phrase,verb,rewritten", VERBLESS)
def test_a_nominalised_outcome_is_flagged_and_converted(statement, phrase, verb, rewritten):
    """
    "Application of IoT..." is worse than a vague verb, not better: there is
    no action at all, so nothing a student does can be marked against it.
    """
    assert detect_issue(statement) == "verbless"
    assert detect_verbless_opening(statement) == phrase
    # The noun the author reached for is the evidence, so it ranks first.
    assert nominal_verb(statement) == verb
    assert suggest_verbs(statement)[0].verb == verb
    assert apply_verb(statement, verb) == rewritten


def test_a_gerund_opening_is_not_an_instruction():
    """
    "Building X" is a noun phrase even though "build" is a fine verb. The
    outcome is not assessable until it says what the student does.
    """
    s = "Building State of the art architecture in IoT."
    assert detect_issue(s) == "verbless"
    assert detect_verbless_opening(s) == "Building"
    # the sentence-initial capital on a common noun goes with the rewrite
    assert apply_verb(s, "develop") == "Develop state of the art architecture in IoT."


def test_a_bare_noun_phrase_gets_an_article():
    """"Explain interface mechanism of..." is not English; "the" is needed."""
    s = ("Interface mechanism of different Sensors, Gateways, and other external "
         "Devices with microcontroller board.")
    assert detect_issue(s) == "verbless"
    assert apply_verb(s, "explain").startswith("Explain the interface mechanism of ")


@pytest.mark.parametrize("statement", [
    # real verbs the Bloom tables happen not to list -- flagging these would
    # tell a faculty member their working outcome is broken
    "Analyse and design basic digital IC circuits using CMOS technology.",
    "Appraise the steady state error of a given transfer function.",
    "Distinguish the concepts of absolute and relative stability.",
    "State various conversion techniques using 8085 & 8086.",
    "Illustrate the programming model of microprocessors.",
    # the same head word used as a verb rather than a noun
    "Interface sensors and actuators with a microcontroller board.",
    "Design for testability of sequential circuits.",
    "Design a low pass filter for a given specification.",
    "Study characteristics of a PN junction diode.",
    "Compare performance of AM and FM systems.",
    "Model behaviour of a second order system.",
    "Measure performance of a microwave bench setup.",
    # an objective-style "To" prefix still has a verb after it
    "To analyze the performance of digital communication systems.",
    "To design equipments related to digital modulation schemes.",
])
def test_an_outcome_that_already_has_a_verb_is_left_alone(statement):
    assert detect_issue(statement) is None
    assert suggest_verbs(statement) == []


@pytest.mark.parametrize("statement,phrase", [
    ("Design of a low pass filter for a given specification.", "Design of"),
    ("Study of characteristics of a PN junction diode.", "Study of"),
    ("Analysis of feedback amplifiers and their frequency response.", "Analysis of"),
    ("Comparison of various modulation techniques.", "Comparison of"),
])
def test_the_preposition_is_what_separates_a_noun_from_an_instruction(statement, phrase):
    """"Design OF logic gates" is a heading; "Design a filter" is an outcome."""
    assert detect_verbless_opening(statement) == phrase


@pytest.mark.parametrize("statement", [
    "Using MATLAB, design a low pass filter for a given specification.",
    "Using modern engineering tools, analyze the response of a system.",
    "Including the effect of noise, evaluate the BER of a link.",
    "Considering ethical constraints, evaluate a design proposal.",
    # not a stop word, but the comma and the verb after it settle it
    "Working in a team, develop a prototype of an IoT node.",
])
def test_a_participial_opener_is_a_modifier_not_a_missing_verb(statement):
    """
    "Using MATLAB, design a filter" already says what the student does.
    Treating the gerund as the fault would strip it and leave
    "Design MATLAB, design a filter".
    """
    assert detect_issue(statement) is None


@pytest.mark.parametrize("statement,rewritten", [
    ("Working of a transistor and its biasing arrangements.",
     "Explain a transistor and its biasing arrangements."),
    ("Understanding the fundamentals of optical communication.",
     "Explain the fundamentals of optical communication."),
    ("Applying the concepts of digital communication to real systems.",
     "Apply the concepts of digital communication to real systems."),
])
def test_a_gerund_that_names_the_activity_still_is_the_fault(statement, rewritten):
    """The comma test cuts both ways: no clause, so the gerund is the subject."""
    assert detect_issue(statement) == "verbless"
    # the preposition the noun was carrying goes with it, as for a vague verb
    assert offline_recommendation(statement)["preview"] == rewritten


def test_the_verbless_rewrite_never_stacks_two_verbs():
    for statement, _phrase, _verb, _out in VERBLESS:
        for s in suggest_verbs(statement, limit=13):
            new = apply_verb(statement, s.verb)
            words = new.lower().split()
            assert words[0] in GUIDELINE_VERBS
            assert words[0] != words[1], new
            assert new.endswith(".")


def test_swapping_a_verb_on_a_healthy_outcome_replaces_it():
    """
    Nothing offers this, but a caller who asks for it must not get
    "Explain Analyze the stability of a feedback system."
    """
    assert (apply_verb("Analyze the stability of a feedback system.", "explain")
            == "Explain the stability of a feedback system.")


# -------------------------------------------- the recommendation, no key

def test_the_recommendation_needs_no_key():
    """
    The deterministic ranking is the recommendation, not a fallback for one.
    It must be complete enough to act on with no network at all.
    """
    rec = offline_recommendation("Understand the fundamentals of IoT.",
                                 blooms_levels=[2])
    assert rec["source"] == "offline"
    assert rec["verb"] == "explain"
    assert rec["bloom_level"] == 2 and rec["action_level"] == 1
    assert rec["preview"] == "Explain the fundamentals of IoT."
    assert rec["reason"]
    assert rec["runner_up"] and rec["runner_up"] != rec["verb"]


def test_the_recommendation_admits_a_tie():
    """
    Saying "either of these is fine" is more useful than implying the first
    was chosen on evidence when the scores are level.
    """
    tie = offline_recommendation("Understand the topics covered in this course.")
    assert tie["clear"] is False
    clear = offline_recommendation("Understand the difference between TDMA and CDMA.")
    assert clear["clear"] is True


def test_nothing_is_recommended_when_nothing_is_wrong():
    assert offline_recommendation("Analyze the stability of a feedback system.") is None


# ------------------------------------------------------------------- API

def test_api_returns_a_ranked_list_and_the_consequences(client):
    r = client.post("/api/verb-advice", json={
        "statement": "Be familiar with the performance of cellular systems.",
        "blooms_levels": [4], "use_llm": False})
    assert r.status_code == 200
    body = r.json()
    assert body["vague_verb"] == "be familiar with"
    assert body["suggestions"], "no suggestions offered"
    first = body["suggestions"][0]
    assert first["verb"] == "analyze"
    assert first["moves_action_level"] is False
    assert first["preview"].startswith("Analyze the performance")


def test_api_says_nothing_when_the_verb_is_already_measurable(client):
    body = client.post("/api/verb-advice", json={
        "statement": "Analyze the stability of a feedback system.",
        "blooms_levels": [4], "use_llm": False}).json()
    assert body["issue"] is None
    assert body["vague_verb"] is None
    assert body["suggestions"] == []
    assert body["recommended"] is None


def test_api_flags_an_outcome_with_no_verb_at_all(client):
    body = client.post("/api/verb-advice", json={
        "statement": "Application of IoT in Industrial and Commercial Building Automation.",
        "blooms_levels": [2], "use_llm": False}).json()
    assert body["issue"] == "verbless"
    assert body["phrase"] == "Application of"
    assert body["vague_verb"] is None          # a different fault, reported as one
    assert body["recommended"]["verb"] == "apply"
    assert body["recommended"]["preview"].startswith("Apply IoT in Industrial")
    # K2 declared, "apply" is K3 -- the row would move, and the API says so
    assert body["suggestions"][0]["moves_action_level"] is True
    assert (body["suggestions"][0]["old_action_level"],
            body["suggestions"][0]["new_action_level"]) == (1, 2)


def test_api_recommends_with_no_key_configured(client, monkeypatch):
    """The recommendation is present with the LLM explicitly switched off."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.post("/api/verb-advice", json={
        "statement": "Designing of logic gates.",
        "blooms_levels": [2], "use_llm": True}).json()
    assert body["ai"] is None
    assert body["recommended"]["verb"] == "design"
    assert body["recommended"]["source"] == "offline"
    assert body["recommended"]["reason"]


def test_the_ai_note_is_absent_rather_than_fabricated(client):
    """
    Without a key the deterministic list is the whole answer. The field is
    present and null so the UI never has to guess whether it asked.
    """
    body = client.post("/api/verb-advice", json={
        "statement": "Understand the fundamentals of IoT.",
        "blooms_levels": [2], "use_llm": True}).json()
    assert "ai" in body
    assert body["ai"] is None or body["ai"]["verb"] in GUIDELINE_VERBS


def test_apply_verb_route_returns_the_new_level(client):
    body = client.post("/api/apply-verb", json={
        "statement": "Understand the fundamentals of IoT.", "verb": "analyze"}).json()
    assert body["statement"] == "Analyze the fundamentals of IoT."
    assert body["new_bloom"] == 4
    assert body["new_action_level"] == 2


def test_apply_verb_route_refuses_a_verb_outside_the_guidelines(client):
    r = client.post("/api/apply-verb", json={
        "statement": "Understand the basics.", "verb": "grok"})
    assert r.status_code == 400


def _published_outcomes():
    from pathlib import Path

    from app.engine.syllabus_parser import infer_missing_bloom_levels, parse_upload

    for name in ("aktu_ece_vii_viii_2026-27.pdf", "aktu_ece_vii_viii_2025-26.pdf",
                 "aktu_ece_v_vi_2025-26.pdf"):
        pdf = Path(__file__).parent / "fixtures" / name
        if not pdf.exists():
            continue
        for course in parse_upload(pdf.name, pdf.read_bytes()).courses:
            for co in infer_missing_bloom_levels(course.cos):
                yield course, co


def test_the_real_schemes_faulty_outcomes_all_get_a_usable_rewrite():
    """
    Every faulty outcome in the three published schemes, rewritten. The bar
    is that the result opens with a guideline verb and reads as a sentence.
    """
    seen = {"vague": 0, "verbless": 0}
    for course, co in _published_outcomes():
        issue = detect_issue(co.statement)
        if not issue:
            continue
        seen[issue] += 1
        best = suggest_verbs(co.statement, blooms_levels=co.blooms_levels)
        assert best, f"{course.code} {co.code}: no suggestion"
        new = apply_verb(co.statement, best[0].verb)
        words = new.split()
        first = words[0].lower()
        assert first in GUIDELINE_VERBS, f"{new!r} opens with {first!r}"
        assert new.endswith(".")
        assert " of of " not in new and " with with " not in new
        assert " the the " not in new.lower()
        assert first != words[1].lower(), new
        # and the recommendation is answerable with no key
        rec = offline_recommendation(co.statement, blooms_levels=co.blooms_levels)
        assert rec["source"] == "offline" and rec["reason"]
    assert seen["vague"] >= 5, f"only {seen['vague']} vague outcomes exercised"
    assert seen["verbless"] >= 5, f"only {seen['verbless']} verbless outcomes exercised"


def test_the_real_schemes_working_outcomes_are_never_flagged():
    """
    Precision is the whole game here. An outcome that already leads with an
    action verb — including British spellings and verbs missing from the
    Bloom tables — must not be told it is broken.
    """
    import re

    from app.engine.co_quality import GOOD_VERBS

    wrongly_flagged = []
    for course, co in _published_outcomes():
        statement = " ".join(co.statement.split())
        head = re.match(r"^\s*(?:[Tt]o\s+)?([A-Za-z]+)", statement)
        if not head or head.group(1).lower() not in GOOD_VERBS:
            continue                       # not a known-good opening; skip
        if statement.lower().startswith(head.group(1).lower() + " of "):
            continue                       # "Design of ..." is a heading
        if detect_issue(statement):
            wrongly_flagged.append(f"{course.code} {co.code}: {statement}")
    assert not wrongly_flagged, "\n".join(wrongly_flagged)
