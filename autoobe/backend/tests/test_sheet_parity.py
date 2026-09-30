"""
How much of the department's own workbook the tool now reproduces.

`test_bloom_inference.py` already pins *level* agreement -- given that a cell
is filled, does Rule 2 land on the department's number. This pins the other
half, which is the half they actually complained about: **is the cell filled
at all**.

The measure is deliberately blunt. For each of the 44 courses in their 2024-25
sheet, take the outcomes they wrote, look the course up in the bundled AKTU
catalogue for its syllabus, run the full path the page runs -- proposal, the
evidence gate, the core floor, the life-long convention -- and count how many
of the cells they filled come back filled.

It is a floor, not a target. Reproducing their sheet exactly is not the goal
and never was: they asked for judgement, and three deliberate departures are
recorded in `test_bloom_inference.py` and `test_non_technical_courses.py`.
What this stops is the opposite failure -- a change that quietly empties
columns they depend on.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.engine.aktu_catalogue import syllabus_for
from app.engine.blooms import infer_bloom_levels
from app.engine.competency import propose_for_co, signal_strength
from app.engine.conventions import (
    NON_TECHNICAL, ConventionConfig, CoreConfig, apply_convention,
    apply_core_columns, columns_for_course, withhold_core_columns,
)
from app.engine.frameworks import get_framework

GOLD = Path(__file__).parent / "fixtures" / "college_copo_2024-25.json"
FW = get_framework("GAPC_V3")


def _gold():
    if not GOLD.exists():
        pytest.skip("college mapping sheet fixture not present")
    return json.loads(GOLD.read_text(encoding="utf-8"))


def _filled(course, *, use_catalogue=True, floor="partial"):
    """The PO cells the tool fills for this course, the way the page fills them."""
    title, code = course["course_title"], course["course_code"]
    syllabus = ""
    if use_catalogue:
        syllabus, _ = syllabus_for(title=title, code=code)
    kind, cols = columns_for_course(title=title, code=code)

    comp, blooms = {}, {}
    for co in course["cos"]:
        text = co["statement"].strip()
        if not text:
            continue
        blooms[co["co"]], _ = infer_bloom_levels(text)
        comp[co["co"]] = {
            p.po_code: int(p.competency)
            for p in propose_for_co(text, framework_code=FW.code,
                                    blooms_levels=blooms[co["co"]],
                                    syllabus=syllabus)}
    evidence = {c["co"]: signal_strength(c["statement"], framework_code=FW.code,
                                         syllabus=syllabus)
                for c in course["cos"] if c["statement"].strip()}
    # The same order the endpoint uses: the course kind takes its engineering
    # columns back before any convention can re-fill them.
    comp, _ = withhold_core_columns(comp, kind=kind)
    r = apply_core_columns(
        comp, co_blooms=blooms, valid_codes=FW.po_action_levels,
        config=CoreConfig(enabled=True, codes=cols, on_conflict="raise",
                          require_evidence=True, unevidenced=floor),
        evidence=evidence, evidence_scope=frozenset(FW.po_codes))
    r = apply_convention(r.competency, co_blooms=blooms, framework_code=FW.code,
                         config=ConventionConfig(enabled=True, competency=2))
    return kind, {(co, po) for co, row in r.competency.items()
                  for po, c in row.items() if c}


def _wanted(course):
    return {(co["co"], po) for co in course["cos"]
            for po, v in co["levels"].items()
            if po.startswith("PO") and isinstance(v, (int, float)) and v}


@pytest.fixture(scope="module")
def parity():
    got = want = 0
    per_kind = Counter()
    per_kind_want = Counter()
    for course in _gold():
        kind, filled = _filled(course)
        w = _wanted(course)
        got += len(w & filled)
        want += len(w)
        per_kind[kind] += len(w & filled)
        per_kind_want[kind] += len(w)
    return got, want, per_kind, per_kind_want


def test_the_sheet_is_reproduced(parity):
    got, want, _, _ = parity
    assert want >= 1200, f"fixture shrank: only {want} filled cells"
    assert got / want >= 0.91, (
        f"cell coverage fell to {got / want:.1%} of the department's sheet")


def test_no_course_kind_carries_the_average(parity):
    """
    91% overall is easy to reach while a whole class of course is empty --
    which is exactly the state the department found the tool in, with every
    technical block near-perfect and Ethics blank on a human values course.
    Each kind is asserted on its own so the aggregate cannot hide one.
    """
    _, _, per, per_want = parity
    for kind in ("theory", "lab", "project"):
        assert per_want[kind] >= 100, f"too few {kind} cells to measure"
        assert per[kind] / per_want[kind] >= 0.92, (
            f"{kind} coverage fell to {per[kind] / per_want[kind]:.1%}")
    # The non-technical half is the work in progress, and it has moved: 44.3%
    # when the catalogue first shipped, 53.3% once the parser learned to read
    # a "Module 1-" heading, a two-code course banner and a bibliography
    # heading with a qualifier in front of it. What is left is mostly courses
    # whose outcomes the department wrote themselves and AKTU never published
    # -- "describe about the entrepreneurial process" is theirs, not the
    # university's. The floor is set where it stands so the number cannot
    # fall, and is meant to keep being raised.
    assert per_want[NON_TECHNICAL] >= 100
    assert per[NON_TECHNICAL] / per_want[NON_TECHNICAL] >= 0.50


def test_the_core_floor_is_what_fills_the_technical_blocks():
    """
    The department's instruction, measured rather than asserted: without the
    floor their technical blocks come back roughly half empty, because five
    outcome sentences rarely mention investigation or modern tool usage even
    in a course that plainly involves both.
    """
    course = next(c for c in _gold() if c["course_code"] == "BEC-302")
    want = _wanted(course)
    _, with_floor = _filled(course, floor="partial")
    _, without = _filled(course, floor="blank")
    assert len(want & with_floor) == len(want)
    assert len(want & without) < len(want) * 0.8


def test_the_catalogue_is_what_fills_the_human_values_block():
    """
    The other half of the same measurement. This course's outcomes reach 13 of
    the 20 cells the department fills; its syllabus -- which nobody uploaded,
    and which now ships with the tool -- reaches all 20.
    """
    course = next(c for c in _gold() if c["course_code"] == "KVE-301")
    want = _wanted(course)
    kind, with_syllabus = _filled(course, use_catalogue=True)
    _, without = _filled(course, use_catalogue=False)
    assert kind == NON_TECHNICAL
    assert len(want & with_syllabus) == len(want)
    assert len(want & without) < len(want)
