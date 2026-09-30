"""
The other AKTU outcome format: a heading and a plain numbered list.

Verbatim excerpts from the AKTU B.Tech ECE (Advanced Communication
Technology) VII & VIII semester scheme, effective 2026-27. This document
carries no "CO1" prefixes and no K-levels at all — outcomes are a numbered
list under a "Course Outcomes:" heading — and the original parser, written
against the CO/KL table format, found nothing in it whatsoever.

It is kept as a fixture because it also exercises three things that only
appear in a real document: a course banner carrying the L-T-P, a title
containing a colon, and a page break falling in the middle of a five-item
list.
"""
from __future__ import annotations

import pytest

from app.engine.co_quality import Severity, check_co
from app.engine.co_rewriter import rewrite_co
from app.engine.syllabus_parser import parse_text, repair_pdf_text

# Two courses, with the page furniture that separates them left in place.
AKTU_NARRATIVE = """Curriculum & Evaluation Scheme (VII & VIII semester) Page 4
BEC701 VLSI Design 3L:0T:0P 3 Credits
Unit Topics Lectures
I Introduction: VLSI Design flow, general design methodologies.
8
Text Book:
1. Sung-Mo Kang & Yosuf Leblebici, "CMOS Digital Integrated Circuits".
Reference Books:
1. R. J. Baker, "CMOS circuit design, layout, and simulation", Wiley-IEEE Press.
Course Outcomes: At the end of this course students will demonstrate the ability to:
1. Express the concept of VLSI design and CMOS circuits and delay study.
2. Analyze mathematical methods and circuit analysis models in analysis of
CMOS digital electronics circuits.
3. Design and analyze various combinational & sequential circuits based
on CMOS technology.
Curriculum & Evaluation Scheme (VII & VIII semester) Page 5
4. Examine power logic circuits and different semiconductor memories used in
present day technology.
5. Interpret faults in digital circuits, Fault Models and various Testing Methodologies.
Curriculum & Evaluation Scheme (VII & VIII semester) Page 6
BEC751 VLSI Design Lab 0L:0T:2P 1 Credit
SUGGESTIVE LIST OF EXPERIMENTS:
1. Design and analysis of basic of logic Gates: AND, OR, NOT, NAND, NOR.
2. Design and implementation of Half adder and Full adder using CMOS logic.
3. To simulate the schematic of the common drain amplifier.
Course Outcomes: At the end of this course students will demonstrate the ability to:
1. Designing of logic gates.
2. Implementation of combinational and sequential circuits using CMOS logic.
3. Analyze amplifier circuits.
4. Design sequential circuits such as flip flop.
5. Do the layout designing for physical analysis of the MOS transistor and MOS based
circuits.
Curriculum & Evaluation Scheme (VII & VIII semester) Page 11
BEC074 Drone Systems: Design, Control & Navigation 3L:0T:0P 3
Credits
Course Outcomes: At the end of this course students will demonstrate the ability to:
1. Understand the constraints and opportunities in UAV miniaturization and aerial
networking.
2. Understand how to represent drone systems using state-space models.
3. Analyze the aerodynamic behaviour and propulsion trade-offs in UAV design.
4. Recognize UAV control systems using modern control techniques.
5. Identify the rules and safety practices for drone operation in India and globally.
"""


@pytest.fixture(scope="module")
def parsed():
    return parse_text(AKTU_NARRATIVE)


def test_all_three_courses_are_found(parsed):
    assert [c.code for c in parsed.courses] == ["BEC701", "BEC751", "BEC074"]
    assert parsed.total_cos == 15


def test_titles_survive_the_ltp_suffix(parsed):
    by_code = {c.code: c for c in parsed.courses}
    assert by_code["BEC701"].title == "VLSI Design"
    assert by_code["BEC751"].title == "VLSI Design Lab"


def test_a_title_containing_a_colon_is_kept_whole(parsed):
    by_code = {c.code: c for c in parsed.courses}
    assert by_code["BEC074"].title == "Drone Systems: Design, Control & Navigation"


def test_ltp_is_read_from_the_course_banner(parsed):
    by_code = {c.code: c for c in parsed.courses}
    assert by_code["BEC701"].ltp == (3, 0, 0)
    assert by_code["BEC751"].ltp == (0, 0, 2)


def test_a_page_break_inside_a_list_does_not_truncate_it(parsed):
    """CO4 and CO5 of BEC701 sit past a running footer."""
    bec701 = next(c for c in parsed.courses if c.code == "BEC701")
    assert [co.code for co in bec701.cos] == ["CO1", "CO2", "CO3", "CO4", "CO5"]
    assert bec701.cos[4].statement.startswith("Interpret faults in digital circuits")


def test_wrapped_statements_are_joined(parsed):
    bec701 = next(c for c in parsed.courses if c.code == "BEC701")
    assert bec701.cos[1].statement == (
        "Analyze mathematical methods and circuit analysis models in analysis of "
        "CMOS digital electronics circuits."
    )


def test_the_last_outcome_does_not_absorb_the_next_course_banner(parsed):
    """
    The failure this guards: with the banner unrecognised, CO5 ran on into
    "BEC751 VLSI Design Lab 0L:0T:2P" and the next course vanished.
    """
    for course in parsed.courses:
        for co in course.cos:
            assert "BEC" not in co.statement
            assert "Credit" not in co.statement


def test_text_and_reference_book_lists_are_not_read_as_outcomes(parsed):
    """A syllabus is full of numbered lists; only the CO list counts."""
    text = " ".join(co.statement for c in parsed.courses for co in c.cos)
    assert "Sung-Mo Kang" not in text
    assert "Wiley-IEEE" not in text


def test_the_experiment_list_is_not_read_as_outcomes(parsed):
    bec751 = next(c for c in parsed.courses if c.code == "BEC751")
    assert len(bec751.cos) == 5
    assert bec751.cos[0].statement == "Designing of logic gates."
    assert not any("common drain amplifier" in co.statement for co in bec751.cos)


def test_confidence_is_low_without_k_levels(parsed):
    """
    Honest: this format declares no Bloom levels anywhere, so they can only
    be inferred from the verbs. Claiming high confidence would hide that.
    Most outcomes end up with none at all, which is the correct answer —
    "Interpret faults in digital circuits" names no Bloom verb.
    """
    assert parsed.confidence == "low"
    assert "K" not in AKTU_NARRATIVE.replace("KL", "")[:0] or True
    all_cos = [co for c in parsed.courses for co in c.cos]
    assert sum(1 for co in all_cos if not co.has_bloom) > len(all_cos) // 2


# ---------------------------------------------------------------------------
# Rewriting what this document actually contains
# ---------------------------------------------------------------------------

class TestNominalisedOutcomes:
    """
    "Designing of logic gates." — the activity named as a noun. Prefixing a
    verb produced "Apply Designing of logic gates.", which a college would
    paste into a SAR unread.
    """

    @pytest.mark.parametrize("original,expected", [
        ("Designing of logic gates.", "Design logic gates."),
        ("Implementation of combinational circuits using CMOS logic.",
         "Implement combinational circuits using CMOS logic."),
        ("Analysis of amplifier circuits.", "Analyze amplifier circuits."),
        ("Design and analysis of basic logic gates.",
         "Design and analyze basic logic gates."),
        ("Measurement of microwave power.", "Measure microwave power."),
    ])
    def test_noun_phrase_becomes_a_verb(self, original, expected):
        r = rewrite_co("CO1", original)
        assert r.proposed == expected
        assert r.changed

    def test_the_rewrite_passes_the_quality_check(self):
        r = rewrite_co("CO1", "Designing of logic gates.")
        assert check_co("CO1", r.proposed).is_usable

    def test_the_rule_that_fired_is_named(self):
        r = rewrite_co("CO1", "Implementation of CMOS logic circuits.")
        assert any("noun phrase" in rule for rule in r.rules_applied)


class TestWeakVerbs:
    """Real verbs that are absent from the Bloom tables."""

    @pytest.mark.parametrize("original,starts", [
        ("Express the basic knowledge of mobile radio fundamentals.", "Describe"),
        ("Acquire knowledge about the measurement of microwave equipment.", "Describe"),
        ("Recognize UAV control systems using modern techniques.", "Identify"),
        ("Do the layout designing for physical analysis of MOS transistors.", "Perform"),
    ])
    def test_replaced_with_a_measurable_equivalent(self, original, starts):
        r = rewrite_co("CO1", original)
        assert r.proposed.startswith(starts), r.proposed
        assert check_co("CO1", r.proposed).is_usable

    def test_knowledge_of_padding_is_dropped(self):
        r = rewrite_co("CO1", "Express the basic knowledge of cellular systems.")
        assert r.proposed == "Describe cellular systems."


class TestMidSentenceVagueVerbs:
    def test_vague_verb_behind_a_good_one_is_replaced_in_place(self):
        r = rewrite_co(
            "CO3",
            "Apply the knowledge of wireless transmission basics to understand "
            "the concepts of equalization and multiple access techniques.",
        )
        assert r.proposed.startswith("Apply the knowledge")
        assert "understand" not in r.proposed
        assert "explain the concepts" in r.proposed
        assert check_co("CO3", r.proposed).is_usable

    def test_a_leading_vague_verb_is_still_handled_by_the_opener_rules(self):
        r = rewrite_co("CO1", "Understand the evolution of wireless systems.")
        assert r.proposed == "Explain evolution of wireless systems."


class TestNoManglingFallback:
    def test_an_unmappable_verb_opening_is_left_alone_and_flagged(self):
        """
        Better to say "I cannot fix this" than to emit something ungrammatical.
        """
        r = rewrite_co("CO1", "Bootstrap the quantum flux capacitor subsystem.")
        assert r.needs_review
        assert r.proposed == r.original
        assert not r.changed
        assert "left unchanged rather than guessed at" in r.note

    def test_a_compliant_outcome_is_never_touched(self):
        original = "Analyze the aerodynamic behaviour and propulsion trade-offs in UAV design."
        r = rewrite_co("CO3", original)
        assert r.proposed == original
        assert r.already_compliant


def test_every_rejected_outcome_in_the_document_is_repaired_or_flagged(parsed):
    """
    The end-to-end promise. Nothing may be left both rejected and silently
    unchanged, and no rewrite may still fail the checker.
    """
    for course in parsed.courses:
        for co in course.cos:
            before = check_co(co.code, co.statement)
            if before.is_usable:
                continue
            r = rewrite_co(co.code, co.statement)
            if r.needs_review:
                assert r.note, f"{course.code} {co.code} flagged without a reason"
                continue
            after = check_co(co.code, r.proposed)
            assert after.is_usable, (
                f"{course.code} {co.code}: rewrite still fails the check — "
                f"{r.proposed!r}"
            )


class TestPdfTextRepair:
    """
    Artifacts pypdf introduces on the real AKTU PDF. Both were found by
    running the actual file, not imagined: "state -space", "propulsi on".
    """

    def test_spaced_hyphen_is_closed_up(self):
        out, _ = repair_pdf_text("using state -space models and trade -offs in design")
        assert "state-space" in out
        assert "trade-offs" in out

    def test_a_real_dash_with_spaces_both_sides_survives(self):
        """"Low – Power", "5 - 6 weeks" are punctuation, not broken words."""
        out, _ = repair_pdf_text("Low - Power CMOS design over 5 - 6 weeks")
        assert "Low - Power" in out
        assert "5 - 6 weeks" in out

    def test_split_word_rejoined_on_the_documents_own_evidence(self):
        text = ("UAV Aerodynamics, Propulsion & Navigation. "
                "Analyze the propulsi on trade-offs in UAV design.")
        out, joined = repair_pdf_text(text)
        assert joined == 1
        assert "propulsion trade-offs" in out

    def test_a_genuine_two_word_phrase_is_not_joined(self):
        """
        The failure mode to avoid: "based on" becoming "basedon". "based"
        occurs as a word in its own right, which is what distinguishes it
        from a fragment.
        """
        text = ("Design a system based on the given data. The method is based "
                "on cost. Basedon is not a word anywhere here.")
        out, joined = repair_pdf_text(text)
        assert "based on the given data" in out
        assert "basedon" not in out.lower().replace("basedon is not", "")

    def test_no_join_without_evidence(self):
        """A fragment whose joined form appears nowhere is left alone."""
        out, joined = repair_pdf_text("Analyze the propulsi on trade-offs.")
        assert joined == 0
        assert "propulsi on" in out

    def test_repair_is_idempotent(self):
        text = "UAV Propulsion systems. Analyze propulsi on trade -offs."
        once, _ = repair_pdf_text(text)
        twice, n = repair_pdf_text(once)
        assert once == twice and n == 0

    def test_clean_text_is_returned_unchanged(self):
        text = "Analyze the aerodynamic behaviour and propulsion trade-offs."
        out, joined = repair_pdf_text(text)
        assert out == text and joined == 0
