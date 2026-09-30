"""
Tests for the deterministic CO->PO mapping rules and CO quality checker,
against the worked examples in "Basic guidelines for framing COs".
"""
from __future__ import annotations

import pytest

from app.engine.blooms import action_level_for_bloom, highest_bloom_level
from app.engine.co_quality import Severity, check_co
from app.engine.mapping_rules import parse_competency, Competency, map_co_to_po, render_level


class TestActionLevels:
    @pytest.mark.parametrize("blooms,expected", [
        ([1], 1), ([2], 1),          # Remember/Understand -> CL1
        ([3], 2), ([4], 2),          # Apply/Analyze       -> CL2
        ([5], 3), ([6], 3),          # Evaluate/Create     -> CL3
    ])
    def test_single_level(self, blooms, expected):
        assert action_level_for_bloom(blooms) == expected

    @pytest.mark.parametrize("blooms,expected", [
        ([2, 3], 2), ([3, 4], 2), ([1, 2], 1), ([3, 6], 3), ([2, 4, 6], 3),
    ])
    def test_multi_level_uses_highest(self, blooms, expected):
        """Real AKTU COs carry tags like 'K3, K4' -- the highest wins."""
        assert action_level_for_bloom(blooms) == expected
        assert highest_bloom_level(blooms) == max(blooms)

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one Bloom"):
            action_level_for_bloom([])

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="1-6"):
            action_level_for_bloom([7])


class TestRule1:
    def test_no_match_is_zero(self):
        r = map_co_to_po(po_code="PO1", competency=Competency.NONE,
                         co_blooms_levels=[6], po_action_level=1)
        assert r.level == 0
        assert "Rule 1" in r.rule_applied

    def test_partial_match_fixed_at_one_regardless_of_bloom(self):
        """Rule 1 fixes partial matches at 1 even for a K6 outcome."""
        r = map_co_to_po(po_code="PO1", competency=Competency.PARTIAL,
                         co_blooms_levels=[6], po_action_level=1)
        assert r.level == 1


class TestRule2:
    @pytest.mark.parametrize("blooms,pl,expected", [
        ([6], 1, 3),   # CL3 >= PL1 -> 3
        ([6], 3, 3),   # CL3 >= PL3 -> 3
        ([3], 2, 3),   # CL2 >= PL2 -> 3
        ([1], 2, 1),   # CL1 <  PL2 -> CL = 1
        ([1], 3, 1),   # CL1 <  PL3 -> CL = 1
        ([3], 3, 2),   # CL2 <  PL3 -> CL = 2
    ])
    def test_action_level_comparison(self, blooms, pl, expected):
        r = map_co_to_po(po_code="PO3", competency=Competency.STRONG,
                         co_blooms_levels=blooms, po_action_level=pl)
        assert r.level == expected
        assert "Rule 2" in r.rule_applied

    def test_shortfall_assigns_the_cos_own_level_not_the_pos(self):
        """
        The branch that is easy to get backwards. A K2 outcome against a PL3
        design PO maps at 1 — what the outcome demonstrates — not at 3.
        Assigning PL would let a weak outcome inherit the PO's strength.
        """
        r = map_co_to_po(po_code="PO3", competency=Competency.STRONG,
                         co_blooms_levels=[2], po_action_level=3)
        assert r.level == 1
        assert r.co_action_level == 1
        assert r.po_action_level == 3
        assert "outcome's own action level" in r.rationale

    def test_mapping_level_never_exceeds_the_cos_action_level_on_a_shortfall(self):
        for bloom in range(1, 7):
            for pl in (1, 2, 3):
                r = map_co_to_po(po_code="PO1", competency=Competency.STRONG,
                                 co_blooms_levels=[bloom], po_action_level=pl)
                cl = r.co_action_level
                assert r.level == (3 if cl >= pl else cl)

    def test_rationale_cites_the_levels(self):
        r = map_co_to_po(po_code="PO3", competency=Competency.STRONG,
                         co_blooms_levels=[3, 4], po_action_level=3)
        assert "[3, 4]" in r.rationale
        assert r.co_action_level == 2

    def test_invalid_po_action_level_rejected(self):
        with pytest.raises(ValueError, match="1-3"):
            map_co_to_po(po_code="PO1", competency=Competency.STRONG,
                         co_blooms_levels=[3], po_action_level=4)


def test_zero_renders_as_dash_in_the_matrix():
    """SAR articulation matrices print '-' for no correlation, never 0."""
    assert render_level(0) == "-"
    assert render_level(1) == "1"
    assert render_level(3) == "3"


class TestCOQuality:
    def test_rejects_the_guidelines_weak_example(self):
        r = check_co("CO1", "To understand digital logic circuits.")
        assert not r.is_usable
        assert any("understand" in f.message.lower() for f in r.findings)

    def test_accepts_the_guidelines_better_example(self):
        r = check_co(
            "CO1",
            "Analyze and design combinational digital circuits using standard "
            "logic design techniques.",
            declared_blooms_levels=[4],
        )
        assert r.is_usable
        assert r.detected_verb == "analyze"

    def test_flags_real_aktu_non_compliant_co(self):
        """Verbatim from a current AKTU syllabus -- not measurable as written."""
        r = check_co("CO1", "Student should be aware of techniques used for classification.")
        assert not r.is_usable

    def test_flags_teacher_intent_phrasing(self):
        r = check_co("CO2", "To provide hardware and software issues in distributed systems.")
        assert not r.is_usable
        assert any(f.check == "Action" for f in r.findings)

    def test_flags_bundled_outcomes(self):
        r = check_co(
            "CO1",
            "Explain, analyze, design and implement communication systems and "
            "evaluate their performance.",
            declared_blooms_levels=[6],
        )
        assert any(f.check == "Specific" and f.severity is Severity.WARNING for f in r.findings)

    def test_flags_multi_bloom_tagging(self):
        r = check_co(
            "CO1", "Apply supervised classification techniques to categorise data sets.",
            declared_blooms_levels=[3, 4],
        )
        assert any(f.check == "Bloom" and "highest" in f.message for f in r.findings)

    def test_always_prompts_for_realism(self):
        r = check_co("CO1", "Design a communication system to satisfy stated requirements.",
                     declared_blooms_levels=[6])
        assert any(f.check == "Realistic" and f.severity is Severity.INFO for f in r.findings)

    def test_empty_statement(self):
        assert not check_co("CO1", "   ").is_usable


class TestNumericCompetency:
    """
    Competency is 0/1/2, matching the guidelines and the matrices faculty
    already fill in. The old names stay accepted so stored data keeps working.
    """

    def test_competency_is_its_number(self):
        assert Competency.NONE == 0
        assert Competency.PARTIAL == 1
        assert Competency.STRONG == 2

    def test_numbers_serialise_as_numbers(self):
        import json
        assert json.dumps({"c": Competency.STRONG}) == '{"c": 2}'

    @pytest.mark.parametrize("value,expected", [
        (0, Competency.NONE), (1, Competency.PARTIAL), (2, Competency.STRONG),
        ("0", Competency.NONE), ("1", Competency.PARTIAL), ("2", Competency.STRONG),
        ("none", Competency.NONE), ("partial", Competency.PARTIAL),
        ("strong", Competency.STRONG), ("STRONG", Competency.STRONG),
        ("  partial  ", Competency.PARTIAL), ("-", Competency.NONE),
    ])
    def test_accepted_spellings(self, value, expected):
        assert parse_competency(value) is expected

    @pytest.mark.parametrize("value", [3, -1, 1.5, "high", "yes", None, True])
    def test_rejected_values_name_the_scale(self, value):
        with pytest.raises(ValueError, match="0 .no match., 1 .partial. or 2 .strong."):
            parse_competency(value)

    def test_numeric_and_named_forms_map_identically(self):
        for numeric, named in ((0, "none"), (1, "partial"), (2, "strong")):
            a = map_co_to_po(po_code="PO1", competency=numeric,
                             co_blooms_levels=[4], po_action_level=3)
            b = map_co_to_po(po_code="PO1", competency=named,
                             co_blooms_levels=[4], po_action_level=3)
            assert a == b

    def test_result_reports_the_competency_that_produced_it(self):
        r = map_co_to_po(po_code="PO1", competency=2, co_blooms_levels=[5],
                         po_action_level=2)
        assert r.competency == 2
        assert r.level == 3

    def test_zero_still_renders_as_a_dash(self):
        r = map_co_to_po(po_code="PO1", competency=0, co_blooms_levels=[5],
                         po_action_level=2)
        assert r.level == 0
        assert r.renders_as == "-"
