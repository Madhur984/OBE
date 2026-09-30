"""
Regression floors for CO -> PO competency inference.

Ratchets, set just below the measured values. Measured on 48 labelled
outcomes (528 CO x PO cells) across ten courses:

              cell acc   false-strong   strong recall   precision   recall
    dev         89.8%        7.4%           83.3%         69.5%      93.2%
    holdout     88.1%        6.2%           60.0%         70.4%      90.5%

The floor that matters is **false strong** -- competency 2 predicted where
the gold is 0 or 1. A strong match runs Rule 2 and normally lands on level
3, and a spurious 3 that gets bulk-approved reaches a SAR. A missed strong
is the same cell proposed at 1 instead: a human bumps it in one click.

Strong recall is deliberately not floored high. The engine is calibrated to
under-propose, and the dev/holdout gap (83% vs 60%) is real generalisation
error, not a bug to tune away on this corpus size.
"""
from __future__ import annotations

import pytest

from app.engine.copo_eval import evaluate, load_corpus

MAX_FALSE_STRONG_RATE = 0.10
MIN_CELL_ACCURACY = 0.85
MIN_ANY_RECALL = 0.86
MIN_ANY_PRECISION = 0.66
MIN_STRONG_RECALL = 0.55


@pytest.fixture(scope="module")
def holdout():
    return evaluate("holdout")


@pytest.fixture(scope="module")
def dev():
    return evaluate("dev")


def test_corpus_is_labelled_and_split():
    fw, dev_cos = load_corpus("dev")
    _, hold_cos = load_corpus("holdout")
    assert fw == "GAPC_V4"
    assert len(dev_cos) >= 15 and len(hold_cos) >= 15
    assert not ({c.course for c in dev_cos} & {c.course for c in hold_cos})


def test_gold_labels_are_valid_competencies():
    _, cos = load_corpus()
    for co in cos:
        assert co.gold, f"{co.course} {co.code} has no gold mapping at all"
        for po, v in co.gold.items():
            assert v in (1, 2), f"{co.course} {co.code} {po}: gold {v} — 0 is implied"
            assert po.startswith("PO")


def test_gold_matrices_are_not_over_mapped():
    """
    A gold set that maps every CO to eight POs would teach the engine the
    exact habit this product exists to break.
    """
    _, cos = load_corpus()
    for co in cos:
        assert len(co.gold) <= 4, f"{co.course} {co.code} claims {len(co.gold)} POs"


def test_false_strong_rate_within_budget(holdout):
    assert holdout.false_strong_rate <= MAX_FALSE_STRONG_RATE, (
        f"False-strong rate {holdout.false_strong_rate:.1%} exceeds "
        f"{MAX_FALSE_STRONG_RATE:.0%}. Each is a spurious level-3 heading for a "
        f"SAR:\n" + "\n".join(
            f"  {e['course']} {e['co']} -> {e['po']} (gold {e['gold']}): "
            f"{e['statement'][:70]}" for e in holdout.false_strong)
    )


def test_cell_accuracy_floor(holdout):
    assert holdout.cell_accuracy >= MIN_CELL_ACCURACY


def test_mapping_recall_floor(holdout):
    """Guards the degenerate fix for false strongs: proposing almost nothing."""
    assert holdout.any_recall >= MIN_ANY_RECALL, (
        f"Mapping recall {holdout.any_recall:.1%} — the engine is missing real "
        f"mappings, which makes it useless even if it is safe."
    )


def test_mapping_precision_floor(holdout):
    assert holdout.any_precision >= MIN_ANY_PRECISION


def test_strong_recall_floor(holdout):
    assert holdout.strong_recall >= MIN_STRONG_RECALL


def test_both_splits_agree_on_the_safety_metric(dev, holdout):
    """
    False-strong rate is what the calibration targets, so it must not be a
    dev-only achievement. Strong recall is allowed to differ — it does, and
    that gap is reported rather than tuned away.
    """
    gap = abs(dev.false_strong_rate - holdout.false_strong_rate)
    assert gap <= 0.08, (
        f"False-strong differs by {gap:.1%} between splits "
        f"(dev {dev.false_strong_rate:.1%}, holdout {holdout.false_strong_rate:.1%})."
    )


def test_evaluation_is_reproducible():
    assert evaluate("holdout").to_dict() == evaluate("holdout").to_dict()


def test_a_recall_level_outcome_never_claims_a_strong_analysis_match():
    """
    The ceiling that made the lexicon safe. "Identify the rules and safety
    practices" is K1; it cannot be a strong problem-analysis match however
    many signal tables contain the word "identify".
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    props = propose_for_co(
        "Identify the rules and safety practices for drone operation in India.",
        framework_code="GAPC_V4", blooms_levels=[1])
    by_po = {p.po_code: p.competency for p in props}
    assert by_po.get("PO2", Competency.NONE) is not Competency.STRONG


def test_a_lexicon_term_does_not_match_inside_a_longer_word():
    """
    Terms anchor to the start of a word. `present` (Communication) was firing
    on "how to REPRESENT drone systems" and `test` (Investigation) on
    "the laTEST standards" — two spurious partials in one real syllabus.
    The right edge stays open: "communicat" must still reach "communication".
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    def pos(stmt, k):
        return {p.po_code: p.competency for p in propose_for_co(
            stmt, framework_code="GAPC_V4", blooms_levels=k)}

    assert pos("Understand how to represent drone systems using state-space "
               "models.", [2]).get("PO9", Competency.NONE) is Competency.NONE
    assert pos("Describe the latest standards for cellular networks.",
               [2]).get("PO4", Competency.NONE) is Competency.NONE
    # The open right edge, still working.
    assert pos("Communicate design decisions in a written report.",
               [2]).get("PO9") is not Competency.NONE


def test_present_counts_as_communication_only_when_it_is_the_action():
    """
    "present day technology" is an adjective; "Present the findings" is the
    outcome's verb. The first must not reach the Communication PO and the
    second must.
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    adjective = {p.po_code for p in propose_for_co(
        "Examine power logic circuits and different semiconductor memories "
        "used in present day technology.",
        framework_code="GAPC_V4", blooms_levels=[4])}
    assert "PO9" not in adjective

    verb = {p.po_code: p.competency for p in propose_for_co(
        "Present the design findings to a technical audience through a report "
        "and a seminar.", framework_code="GAPC_V4", blooms_levels=[3])}
    assert verb.get("PO9") is Competency.STRONG


def test_subject_matter_is_not_read_as_the_action_verb():
    """
    "Express the concept of VLSI design" mapped strongly onto the design PO
    because "design" appears as the sixth word. The verb is the one that
    opens the outcome.
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    props = propose_for_co(
        "Describe the concept of VLSI design and CMOS circuits and delay study.",
        framework_code="GAPC_V4", blooms_levels=[2])
    by_po = {p.po_code: p.competency for p in props}
    assert by_po.get("PO3", Competency.NONE) is not Competency.STRONG
    assert by_po.get("PO1") is Competency.STRONG


def test_specialisation_nouns_cannot_reach_a_strong_match():
    """
    Naming the topic says the outcome is inside the discipline — a partial
    engineering-knowledge match. It does not say the outcome demonstrates
    engineering-knowledge competency. Scoring the two together let four
    stacked domain nouns clear the strong threshold on subject matter alone,
    taking held-out false strongs from 6.7% to 15.8%.
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    dense = ("Examine semiconductor memory, transistor logic, amplifier and "
             "antenna behaviour across the radio frequency spectrum.")
    by_po = {p.po_code: p.competency for p in propose_for_co(
        dense, framework_code="GAPC_V4", blooms_levels=[4])}
    assert by_po.get("PO1") is Competency.PARTIAL

    # ...while the fundamentals vocabulary still reaches strong on its own.
    principled = ("Apply the engineering fundamentals and first principles of "
                  "electromagnetic theory to derive the governing equations.")
    by_po = {p.po_code: p.competency for p in propose_for_co(
        principled, framework_code="GAPC_V4", blooms_levels=[3])}
    assert by_po.get("PO1") is Competency.STRONG


def test_the_po_vocabulary_covers_its_own_verbatim_subject_matter():
    """
    Each of these was a silent zero — the engine had no term for the thing the
    PO statement is literally about, so the cell could never be proposed at
    all. Added from the verbatim PO/WK text, not from corpus misses.
    """
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    def po(stmt, k, code):
        return {p.po_code: p.competency for p in propose_for_co(
            stmt, framework_code="GAPC_V4", blooms_levels=k)}.get(code, Competency.NONE)

    # PO6 "environmental aspects ... impact on sustainability"
    assert po("Explain the structure of ecosystems and the impact of engineering "
              "activity on biodiversity.", [2], "PO6") is not Competency.NONE
    # PO7 "adhere to national & international laws"
    assert po("Identify the rules and safety practices for drone operation in "
              "India, including licensing and airspace regulation.", [1],
              "PO7") is not Competency.NONE
    # PO11 "adaptability to new and emerging technologies"
    assert po("Explain the evolution of wireless communication systems and the "
              "requirements of 5G technology.", [2], "PO11") is not Competency.NONE


def test_the_bare_word_law_is_not_an_ethics_signal():
    """"The first law of thermodynamics" is not a statute."""
    from app.engine.competency import propose_for_co
    from app.engine.mapping_rules import Competency

    by_po = {p.po_code: p.competency for p in propose_for_co(
        "Apply the first law of thermodynamics to closed and open systems.",
        framework_code="GAPC_V4", blooms_levels=[3])}
    assert by_po.get("PO7", Competency.NONE) is Competency.NONE
