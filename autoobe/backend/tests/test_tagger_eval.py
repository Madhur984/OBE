"""
Regression floors for the question -> CO tagger.

These are ratchets, not aspirations. They are set just below the measured
values so an honest improvement passes and a regression fails loudly. When a
change improves the numbers, raise the floors in the same commit -- a ratchet
nobody tightens stops being one.

Measured at the time of writing, on all 87 labelled questions:

    accuracy            72.4%
    coverage            70.1%
    precision @ high    86.9%
    silent error rate    9.2%

The floor that matters is the silent error rate. Accuracy can drop a little
without anyone being harmed -- a flagged question costs a faculty member ten
seconds. A confidently mistagged question feeds the attainment arithmetic and
moves a SAR figure nobody re-checks.
"""
from __future__ import annotations

import pytest

from app.engine.tagger_eval import evaluate, load_corpus

MAX_SILENT_ERROR_RATE = 0.12
MIN_PRECISION_AT_HIGH = 0.82
MIN_ACCURACY = 0.68
MIN_COVERAGE = 0.62


@pytest.fixture(scope="module")
def full():
    return evaluate()


def test_corpus_is_large_enough_to_mean_something():
    fixtures = load_corpus()
    questions = sum(len(f.questions) for f in fixtures)
    assert len(fixtures) >= 5, "Fewer courses than branches we claim to support."
    assert questions >= 80, (
        f"Only {questions} labelled questions. Below this the metrics move by "
        f"more than a percentage point per question and cannot detect a "
        f"regression."
    )


def test_corpus_has_a_holdout_split():
    """
    The split is what makes the numbers mean anything. Without it, tuning the
    scorer against the corpus measures how well it was fitted to its own test
    set -- which is exactly the failure this suite exists to prevent.
    """
    dev = load_corpus(split="dev")
    holdout = load_corpus(split="holdout")
    assert dev and holdout, "Both splits must be populated."
    assert not ({c.code for c in dev} & {c.code for c in holdout}), \
        "A course appears in both splits; the holdout is contaminated."
    assert sum(len(c.questions) for c in holdout) >= 30


def test_gold_labels_reference_real_cos():
    """A typo in a gold label silently makes a question unanswerable."""
    for fx in load_corpus():
        for q in fx.questions:
            assert q.gold_co in fx.cos, (
                f"{fx.code} {q.question_no} is labelled {q.gold_co}, which is "
                f"not a CO of that course."
            )


def test_silent_error_rate_within_floor(full):
    assert full.silent_error_rate <= MAX_SILENT_ERROR_RATE, (
        f"Silent error rate {full.silent_error_rate:.1%} exceeds "
        f"{MAX_SILENT_ERROR_RATE:.0%}. These are questions tagged with high "
        f"confidence and tagged wrong, so they reach attainment unreviewed:\n"
        + "\n".join(
            f"  [{e['course']}] {e['question_no']} gold={e['gold']} "
            f"got={e['predicted']}: {e['text'][:70]}"
            for e in full.silent_errors
        )
    )


def test_precision_on_confident_tags(full):
    assert full.precision_at_high >= MIN_PRECISION_AT_HIGH, (
        f"Precision on high-confidence tags is {full.precision_at_high:.1%}, "
        f"below {MIN_PRECISION_AT_HIGH:.0%}. The tagger's self-assessment has "
        f"drifted from its accuracy — re-sweep HIGH_MARGIN."
    )


def test_accuracy_floor(full):
    assert full.accuracy >= MIN_ACCURACY, \
        f"Accuracy {full.accuracy:.1%} below floor {MIN_ACCURACY:.0%}."


def test_coverage_floor(full):
    """
    Guards the degenerate optimisation: driving silent errors to zero by
    flagging every question for review. That passes the safety floor and is
    not a product.
    """
    assert full.coverage >= MIN_COVERAGE, (
        f"Coverage {full.coverage:.1%} below {MIN_COVERAGE:.0%}. The tagger is "
        f"punting to a human too often to save anyone time."
    )


def test_both_splits_behave_similarly():
    """
    A large dev/holdout gap means the scorer was fitted to the dev vocabulary.
    This caught exactly that during tuning: a settings sweep took dev silent
    errors to 5.0% while holdout rose to 12.8%.
    """
    dev = evaluate(split="dev")
    holdout = evaluate(split="holdout")
    gap = abs(dev.silent_error_rate - holdout.silent_error_rate)
    assert gap <= 0.10, (
        f"Silent error rate differs by {gap:.1%} between splits "
        f"(dev {dev.silent_error_rate:.1%}, holdout "
        f"{holdout.silent_error_rate:.1%}). That gap is overfitting to dev, "
        f"not a better scorer."
    )


def test_evaluation_is_reproducible():
    """No randomness anywhere in the deterministic path."""
    a, b = evaluate(), evaluate()
    assert a.to_dict() == b.to_dict()


def test_no_course_is_catastrophically_wrong(full):
    """
    Aggregate accuracy can hide one course being useless. A course below 50%
    means the tagger is worse than a coin flip there, and the aggregate is
    carrying it.
    """
    bad = {c: m for c, m in full.per_course.items() if m["accuracy"] < 0.5}
    assert not bad, f"Course-level accuracy below 50%: {bad}"
