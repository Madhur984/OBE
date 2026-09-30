"""
The real AKTU PDF, end to end: syllabus -> outcomes -> Bloom's -> CO x PO.

`tests/fixtures/aktu_ece_vii_viii_2026-27.pdf` is the actual B.Tech ECE
(Advanced Communication Technology) VII & VIII semester scheme, effective
2026-27, exactly as the university publishes it. This is the input format
the product is built around, so it is pinned here rather than paraphrased:
a paraphrase drifts, and every parser bug found so far came from something
only the real file does.

What this file guards, specifically:

* **The K column is absent.** This scheme lists outcomes as a numbered list
  under a "Course Outcomes:" heading with no Bloom's tags whatsoever. Every
  level therefore comes from our reading of the verb, and every level must
  say so -- `bloom_inferred` was silently always False, because inference
  fills `blooms_levels` in place and `has_bloom` then reports "declared".
  A guessed K feeds CL, and CL decides the mapping level under Rule 2.
* **Subject matter is not the action verb.** "Express the concept of VLSI
  design" must not read `design` as the verb and claim a strong level-3 on
  the design PO.
* **Six courses stay six courses.** CO1 of VLSI Design must never merge
  with CO1 of Drone Systems.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.competency import propose_for_co
from app.engine.frameworks import get_framework
from app.engine.mapping_rules import Competency, map_co_to_po
from app.engine.syllabus_parser import infer_missing_bloom_levels, parse_upload

PDF = Path(__file__).parent / "fixtures" / "aktu_ece_vii_viii_2026-27.pdf"

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="AKTU fixture PDF not present")

EXPECTED = {
    "BEC701": ("VLSI Design", (3, 0, 0)),
    "BEC751": ("VLSI Design Lab", (0, 0, 2)),
    "BEC071": ("Wireless and Mobile Communication", (3, 0, 0)),
    "BEC073": ("Microwave & Radar Engineering", (3, 0, 0)),
    "BEC074": ("Drone Systems: Design, Control & Navigation", (3, 0, 0)),
    "BEC075": ("5G Technology for Wireless Communication", (3, 0, 0)),
}


@pytest.fixture(scope="module")
def syllabus():
    return parse_upload(PDF.name, PDF.read_bytes())


@pytest.fixture(scope="module")
def courses(syllabus):
    return {c.code: c for c in syllabus.courses}


def test_every_course_in_the_scheme_is_found(courses):
    assert set(courses) == set(EXPECTED)


def test_titles_and_credits_survive_the_page_furniture(courses):
    for code, (title, ltp) in EXPECTED.items():
        c = courses[code]
        assert c.title == title
        assert c.ltp == ltp, f"{code} L-T-P"


def test_each_course_keeps_its_own_five_outcomes(courses):
    for code, c in courses.items():
        assert len(c.cos) == 5, f"{code} has {len(c.cos)} outcomes"
        assert [o.code for o in c.cos] == [f"CO{i}" for i in range(1, 6)]
    # The merge failure this guards against: identical CO1 text everywhere.
    first = {c.cos[0].statement for c in courses.values()}
    assert len(first) == len(courses)


def test_this_scheme_declares_no_bloom_levels(courses):
    """
    Nothing in this PDF carries a K column. If one ever appears to, the
    K-level regex has started matching something it should not.
    """
    for c in courses.values():
        for co in c.cos:
            assert not co.blooms_levels, f"{c.code} {co.code} claims declared K levels"


def test_inferred_levels_are_labelled_as_inferred(courses):
    """
    The flag that was silently always False. Faculty cannot sanity-check a
    level that presents itself as the university's when it is ours.
    """
    for c in courses.values():
        for co in infer_missing_bloom_levels(c.cos):
            assert co.blooms_levels, f"{c.code} {co.code} got no level at all"
            assert co.bloom_inferred is True
            assert co.bloom_declared is False


def test_subject_matter_is_not_read_as_the_action_verb(courses):
    """
    BEC701 CO1: "Express the concept of VLSI design and CMOS circuits…".
    `design` is the sixth word and the topic, not the action. Reading it as
    the verb gave K6 -> CL 3 -> a strong level-3 claim on the design PO.
    """
    co1 = infer_missing_bloom_levels(courses["BEC701"].cos)[0]
    assert co1.statement.startswith("Express the concept of VLSI design")
    assert co1.blooms_levels == [2]

    props = {p.po_code: p.competency for p in propose_for_co(
        co1.statement, framework_code="GAPC_V4", blooms_levels=co1.blooms_levels)}
    assert props.get("PO3", Competency.NONE) is not Competency.STRONG


def test_a_k2_outcome_cannot_reach_level_3_on_a_design_po(courses):
    """Rule 2's second branch, on a real outcome: CL 1 < PL 3 gives 1, not 3."""
    fw = get_framework("GAPC_V4")
    co1 = infer_missing_bloom_levels(courses["BEC701"].cos)[0]
    res = map_co_to_po(po_code="PO3", competency=Competency.STRONG,
                       co_blooms_levels=co1.blooms_levels,
                       po_action_level=fw.po_action_levels["PO3"])
    assert (res.co_action_level, res.po_action_level) == (1, 3)
    assert res.level == 1


def test_the_whole_scheme_maps_without_over_claiming(courses):
    """
    Every outcome in the file, through the real pipeline. The engine is
    calibrated to under-propose, so the guard is on the upper end: no course
    may come out with a wall of strong level-3 cells.
    """
    fw = get_framework("GAPC_V4")
    for code, c in courses.items():
        levels: dict[str, dict[str, int]] = {}
        for co in infer_missing_bloom_levels(c.cos):
            row: dict[str, int] = {}
            for p in propose_for_co(co.statement, framework_code="GAPC_V4",
                                    blooms_levels=co.blooms_levels)[:4]:
                if p.competency is Competency.NONE:
                    continue
                res = map_co_to_po(po_code=p.po_code, competency=p.competency,
                                   co_blooms_levels=co.blooms_levels,
                                   po_action_level=fw.po_action_levels[p.po_code])
                if res.level:
                    row[p.po_code] = res.level
            assert len(row) <= 4, f"{code} {co.code} maps to {len(row)} POs"
            levels[co.code] = row

        assert any(levels.values()), f"{code} produced an entirely empty matrix"
        cells = [v for row in levels.values() for v in row.values()]
        assert all(1 <= v <= 3 for v in cells)
        threes = sum(1 for v in cells if v == 3)
        assert threes <= len(cells) * 0.75, (
            f"{code}: {threes} of {len(cells)} cells are level 3 — that is the "
            f"over-mapped matrix this product exists to prevent"
        )


def test_the_api_reports_inference_to_the_ui():
    """The UI badge reads this field; it is the only signal a level is a guess."""
    from fastapi.testclient import TestClient

    from app.api.main import app

    r = TestClient(app).post(
        "/api/parse-syllabus",
        files={"file": (PDF.name, PDF.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["total_cos"] == 30
    assert len(body["courses"]) == 6
    for c in body["courses"]:
        for co in c["cos"]:
            assert co["blooms_levels"], f"{c['code']} {co['code']}"
            assert co["bloom_inferred"] is True
    assert any("Bloom" in w for w in body["warnings"])
