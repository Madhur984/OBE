"""
Reading the Bloom level off the verb an outcome actually opens with.

The scanner in `blooms.leading_action_levels` deliberately reads only the
*leading* verb phrase — scanning further mistakes subject matter for the
action and produces the spurious level-3 design mappings the whole engine is
calibrated against. That restraint has a cost: anything standing in front of
the verb stops the scan dead, and the outcome silently falls back to the K2
default.

Three things were standing in front of the verb in real syllabi, and each one
was worth measuring rather than assuming:

  * "Students will be able to design..."  — 45 of the 214 outcomes in the
    college's own 2024-25 mapping sheet, 17 landing on the wrong action level
  * "Effectively write the report"        — an adverb is never the action
  * "Analyse RLC circuits"                — British spelling, absent from the
    Bloom tables, so a K4 outcome scored K2

CL is not cosmetic: under Rule 2 a strong match with CL < PL maps at CL, so
an outcome floored at CL 1 drags every strong cell in its row down to 1.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.engine.blooms import (
    action_level_for_bloom, infer_bloom_levels, leading_action_levels,
)
from app.engine.frameworks import get_framework
from app.engine.mapping_rules import Competency, map_co_to_po

GOLD = Path(__file__).parent / "fixtures" / "college_copo_2024-25.json"


@pytest.mark.parametrize("statement,levels", [
    ("Students will be able to design CMOS digital integrated circuits.", [6]),
    ("Students will be able to apply the concept of Op-Amp based circuits.", [3]),
    ("Students will demonstrate the ability to formulate basic statistics.", [6]),
    ("The students should be able to evaluate different modulation schemes.", [5]),
    ("Student will be able to explain and design oscillators.", [2, 6]),
    ("At the end of the course, analyze the stability of a system.", [4]),
    ("On completion of the course students will be able to identify faults.", [1]),
])
def test_a_preamble_does_not_hide_the_verb(statement, levels):
    assert leading_action_levels(statement) == levels
    assert infer_bloom_levels(statement) == (levels, False)


def test_the_preamble_rule_does_not_invent_a_verb():
    """Strip the preamble and there may still be nothing to read."""
    assert leading_action_levels("Students will be able to IoT architecture.") == []
    assert infer_bloom_levels("Students will be able to IoT architecture.") == ([2], True)


@pytest.mark.parametrize("statement,levels", [
    ("Effectively write the learnt simulation principles in form of report.", [3]),
    ("Practically demonstrate the working of a transistor.", [3]),
    ("Critically evaluate a design proposal.", [5]),
])
def test_a_leading_adverb_is_not_the_action(statement, levels):
    assert leading_action_levels(statement) == levels


def test_the_adverb_rule_never_eats_apply():
    """
    "Apply" ends in "ly". A `[a-z]+ly` pattern strips it, which floors the
    single most common verb in an AKTU outcome at the K2 default and drags
    its whole mapping row down. This is the regression that pattern caused.
    """
    from app.engine.blooms import _LEADING_ADVERB

    for word in ("Apply", "Multiply", "Supply", "Imply", "Rely"):
        assert not _LEADING_ADVERB.match(f"{word} the thing."), f"{word} was stripped"
    assert leading_action_levels("Apply the concept of sampling.") == [3]
    assert leading_action_levels(
        "Apply knowledge of mathematics to solve engineering problems.") == [3]


@pytest.mark.parametrize("statement,expected", [
    ("Analyse RLC circuits.", 4),
    ("Analyse and design basic digital IC circuits using CMOS technology.", 6),
    ("Distinguish the concepts of absolute and relative stability.", 4),
    ("Determine the stability of an electrical circuit.", 3),
    ("Appraise the steady state error of a given transfer function.", 5),
    ("Utilise modern tools for circuit simulation.", 3),
    ("Derive the transfer function of a second order system.", 3),
    ("Sketch the radiation pattern of a dipole antenna.", 3),
])
def test_verbs_the_bloom_tables_do_not_list_are_still_read(statement, expected):
    """
    A British spelling is the same verb. Reading "Analyse" as K2 because the
    table spells it "analyze" is a spelling bug wearing a taxonomy costume.
    """
    assert max(infer_bloom_levels(statement)[0]) == expected


def test_the_default_still_cannot_over_claim():
    """
    None of the above may turn the K2 floor into a guess that reaches K6.
    An outcome wrongly floored low costs a human one correction; one wrongly
    raised produces a level-3 design mapping that a bulk approval carries
    into a SAR.
    """
    levels, guessed = infer_bloom_levels("Fundamentals of semiconductor physics.")
    assert (levels, guessed) == ([2], True)


# ------------------------------------------------- measured, not asserted

def _gold():
    if not GOLD.exists():
        pytest.skip("college mapping sheet fixture not present")
    return json.loads(GOLD.read_text(encoding="utf-8"))


def _technical(course):
    """The college maps PO1-PO5 only for technical courses."""
    return any(co["levels"].get("PO1") is not None for co in course["cos"])


def test_most_outcomes_in_the_real_sheet_now_yield_a_read_verb():
    """
    Before the three fixes above, 105 of the 184 outcomes in the college's
    technical courses fell through to the K2 default — more than half the
    sheet was mapped off a guess. This pins the improvement so it cannot
    quietly regress.
    """
    guessed = total = 0
    for course in _gold():
        if not _technical(course):
            continue
        for co in course["cos"]:
            if not co["statement"].strip():
                continue
            total += 1
            guessed += infer_bloom_levels(co["statement"])[1]
    assert total >= 150, f"fixture shrank: only {total} outcomes"
    assert guessed <= 30, f"{guessed}/{total} outcomes still fall back to the default"


def test_the_rules_reproduce_the_colleges_own_levels():
    """
    A parity floor, not a claim of correctness.

    Holding competency at STRONG — which is what the college's own template
    asserts for a technical course — Rule 1 and Rule 2 reproduce the levels
    in their published 2024-25 sheet for 76.8% of cells:

        PO1 80.2%   PO2 80.2%   PO3 74.7%   PO4 81.9%   PO12 100%
        PO5 44.0%   <- deliberate, and the whole of the drop from 83%

    The floor is 0.75 rather than 0.82 on purpose. The department asked for
    modern tool usage to read as a moderate contribution, "like a human does
    it", so PO5 now carries PL 3 from its own opening verb ("CREATE, select
    and apply"), which maps an apply-level outcome at 2. Their 2024-25 sheet
    records that column flat at 3, so agreement there falls by design — the
    newer instruction outranks the older artefact. Every other column is
    unchanged, which is the check that matters: the drop is one deliberate
    decision, not a general loosening.
    `test_po5_reads_as_a_moderate_contribution_not_a_substantial_one` pins
    the intent so this floor cannot drift back by accident.

    What is left beyond that is one thing, not a scatter: outcomes at action
    level 1 ("Explain…", "Describe…") that the sheet records at 2 or 3. Rule
    2's `CL < PL -> CL` branch caps those at 1 — and the sheet applies that
    same cap in places and not others, inside one course block. That is a
    disagreement inside the sheet, not a defect here, and it is surfaced to
    the user rather than tuned away.
    """
    fw = get_framework("GAPC_V3")
    cols = ["PO1", "PO2", "PO3", "PO4", "PO5", "PO12"]
    agree = total = 0
    per = Counter()
    per_total = Counter()
    for course in _gold():
        if not _technical(course):
            continue
        for co in course["cos"]:
            if not co["statement"].strip():
                continue
            blooms, _ = infer_bloom_levels(co["statement"])
            for po in cols:
                want = co["levels"].get(po)
                if not isinstance(want, (int, float)):
                    continue
                got = map_co_to_po(
                    po_code=po, competency=Competency.STRONG,
                    co_blooms_levels=blooms,
                    po_action_level=fw.po_action_levels[po]).level
                total += 1
                per_total[po] += 1
                if int(want) == got:
                    agree += 1
                    per[po] += 1
    assert total >= 900, f"fixture shrank: only {total} cells"
    assert agree / total >= 0.75, f"level agreement fell to {agree / total:.1%}"
    # Every column except the one deliberately changed holds its old ground.
    # Without this the 0.75 floor would quietly absorb a real regression.
    for po in ("PO1", "PO2", "PO3", "PO4"):
        assert per[po] / per_total[po] >= 0.74, (
            f"{po} fell to {per[po] / per_total[po]:.1%} — PO5 is the only "
            f"column that was meant to move")
    # Life-long learning has PL 1, so CL >= PL always holds and Rule 2 gives 3
    # for every strong match -- exactly what the sheet records, every time.
    assert per["PO12"] == per_total["PO12"]
    # PO4's action level is 2, not 3: the PO asks the student to *use*
    # research-based knowledge, and the only "design" in its statement is the
    # noun phrase "design of experiments". With PL 3 this column scored 42.9%
    # against the department's own matrix; with PL 2 it scores above 80%.
    assert per["PO4"] / per_total["PO4"] >= 0.78


def test_po5_reads_as_a_moderate_contribution_not_a_substantial_one():
    """
    The department's instruction, in one assertion: "modern tool usage is
    generally partial ... map it like a human does it".

    PO5 opens "CREATE, select, and apply ... modern engineering and IT tools"
    — create is K6 and the highest of the three, so PL 3, exactly as PO3 gets
    PL 3 from "Design solutions". Rule 2 then does the rest, per outcome
    rather than flatly:

      * an apply- or analyse-level outcome (CL 2) maps PO5 at 2
      * an outcome that actually builds something (CL 3) still reaches 3

    That is the whole mechanism. Nothing special-cases PO5; it is the same
    rule that already made PO3 differentiate.
    """
    fw = get_framework("GAPC_V3")
    assert fw.po_action_levels["PO5"] == 3
    assert get_framework("GAPC_V4").po_action_levels["PO5"] == 3

    def lvl(statement, po):
        blooms, _ = infer_bloom_levels(statement)
        return map_co_to_po(po_code=po, competency=Competency.STRONG,
                            co_blooms_levels=blooms,
                            po_action_level=fw.po_action_levels[po]).level

    applied = "Perform numerous arithmetic and logic simplification using various methods."
    built = "Design various ADCs and DACs according to the given specifications."
    assert lvl(applied, "PO5") == 2      # moderate, not substantial
    assert lvl(built, "PO5") == 3        # earned by the outcome's own verb
    # and PO1, which only ever asks the graduate to "Apply", is untouched
    assert lvl(applied, "PO1") == 3


def test_the_hand_built_digital_system_design_matrix_reproduces_exactly():
    """
    The client supplied this one course's matrix as the standard to hit,
    calling it hand-made and near-perfect. Every cell of it falls out of
    Rule 1 + Rule 2 with competency held at STRONG — including the cells that
    are not a 3.

    One cell deliberately departs from their sheet: CO1's PO5. Their sheet
    records 3; an outcome that *performs simplification* is CL 2 against
    PO5's PL 3, so it maps at 2. That is the department's later instruction
    that tool usage should read as a moderate contribution, and it is marked
    here rather than quietly dropped from the comparison.
    """
    fw = get_framework("GAPC_V3")
    cols = ["PO1", "PO2", "PO3", "PO4", "PO5", "PO12"]
    # BEC-302, verbatim from "All CO-PO Mapping 2024-25.xlsx"; the marked cell
    # is the one place this now differs, and their value is kept beside it.
    manual = {
        "Perform numerous arithmetic and logic simplification using various methods.":
            [3, 3, 2, 3, 2, 3],           # their sheet: PO5 = 3
        "Design and analyze modular combinational circuits with MUX / DEMUX, Decoder & Encoder.":
            [3, 3, 3, 3, 3, 3],
        "Design & analyze synchronous sequential logic circuits.":
            [3, 3, 3, 3, 3, 3],
        "Analyze various logic families and design circuits using PLDs.":
            [3, 3, 3, 3, 3, 3],
        "Design various ADCs and DACs according to the given specifications.":
            [3, 3, 3, 3, 3, 3],
    }
    for statement, wanted in manual.items():
        blooms, _ = infer_bloom_levels(statement)
        got = [map_co_to_po(po_code=po, competency=Competency.STRONG,
                            co_blooms_levels=blooms,
                            po_action_level=fw.po_action_levels[po]).level
               for po in cols]
        assert got == wanted, f"{statement!r}\n  got {got}\n  want {wanted}"


def test_a_second_action_joined_by_and_is_read():
    """
    "Analyze various logic families AND DESIGN circuits using PLDs" claims
    K6, and the department's matrix maps it at the design level. The leading
    phrase alone reads only K4.
    """
    assert infer_bloom_levels(
        "Analyze various logic families and design circuits using PLDs.")[0] == [4, 6]
    assert infer_bloom_levels(
        "To identify different line coding techniques and demonstrate the concepts."
    )[0] == [1, 3]


@pytest.mark.parametrize("statement,levels", [
    # the failure the leading-phrase rule exists to prevent: subject matter
    # read as an action. "design" here is a noun in "the concept of VLSI design".
    ("Express the concept of VLSI design and CMOS circuits and delay study.", [2]),
    # a verb followed directly by "of" is a nominalisation, not a second action
    ("Demonstrate the working principle and design of bipolar junction transistor.", [3]),
    ("Explain the construction and operation of a MOSFET.", [2]),
])
def test_subject_matter_is_still_not_read_as_a_second_action(statement, levels):
    assert infer_bloom_levels(statement)[0] == levels


def test_the_action_level_floor_did_not_move_the_wrong_way():
    """
    Reading more verbs must raise action levels, never lower them. A verb
    added at the wrong Bloom level would show up here as an outcome the
    scanner now reads *below* the K2 floor it used to get for free.
    """
    lowered = []
    for course in _gold():
        for co in course["cos"]:
            statement = co["statement"].strip()
            if not statement:
                continue
            levels, guessed = infer_bloom_levels(statement)
            if not guessed and action_level_for_bloom(levels) < 1:
                lowered.append(statement)
    assert not lowered
