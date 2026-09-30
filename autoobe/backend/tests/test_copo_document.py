"""
The CO -> PO document, and the PO averages it carries.

The average row is the line a peer team reads first, and the denominator is
the thing institutions get wrong. These tests pin both denominators against
worked examples rather than against whatever the code currently returns.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.copo_document import (
    articulation_table, averages_table, build_copo_document, coverage_notes,
    po_averages,
)
from app.engine.frameworks import get_framework

FW = get_framework("GAPC_V4")
POS = list(FW.po_codes)

COS = [
    {"code": "CO1", "statement": "Express the concept of VLSI design.",
     "blooms_levels": [2], "bloom_inferred": True},
    {"code": "CO2", "statement": "Analyze CMOS circuits and delay.",
     "blooms_levels": [4], "bloom_inferred": True},
    {"code": "CO3", "statement": "Design sequential circuits.",
     "blooms_levels": [6], "bloom_inferred": False},
    {"code": "CO4", "statement": "Interpret faults in digital circuits.",
     "blooms_levels": [4], "bloom_inferred": True},
]
# PO1: 1, 3, -, 1  -> mapped 3, total 5
# PO2: -, 3, 3, 3  -> mapped 3, total 9
# PO3: 1, -, 3, -  -> mapped 2, total 4
LEVELS = {
    "CO1": {"PO1": 1, "PO3": 1},
    "CO2": {"PO1": 3, "PO2": 3},
    "CO3": {"PO2": 3, "PO3": 3},
    "CO4": {"PO1": 1, "PO2": 3},
}


@pytest.fixture()
def client():
    return TestClient(app)


def _avg(code):
    return next(a for a in po_averages(
        LEVELS, po_codes=POS, po_titles=FW.po_titles,
        po_action_levels=FW.po_action_levels,
        co_codes=[c["code"] for c in COS]) if a.po_code == code)


def test_average_over_mapped_divides_by_the_mapped_cos():
    """PO1 is 1 + 3 + 1 over three COs, not over four."""
    a = _avg("PO1")
    assert (a.mapped_cos, a.total_cos, a.total_level) == (3, 4, 5)
    assert a.average_over_mapped == pytest.approx(5 / 3)


def test_average_over_all_counts_an_unmapped_cell_as_zero():
    a = _avg("PO1")
    assert a.average_over_all == pytest.approx(5 / 4)


def test_the_two_denominators_are_genuinely_different():
    """
    If these ever coincide the tests stop proving anything. PO2 is untouched
    by CO1, so its two means must differ.
    """
    a = _avg("PO2")
    assert a.average_over_mapped == pytest.approx(3.0)
    assert a.average_over_all == pytest.approx(2.25)


def test_an_untouched_po_has_no_average_rather_than_a_zero():
    """
    0.00 would read as "addressed and attained nothing". A PO no outcome
    touches has no mean at all, and the document prints a dash.
    """
    a = _avg("PO9")
    assert a.mapped_cos == 0
    assert a.average_over_mapped is None
    assert a.rounded is None
    assert a.average_over_all == 0.0


def test_rounding_is_reported_beside_the_number_it_came_from():
    a = _avg("PO1")                      # 1.667
    assert a.rounded == 2
    row = next(r for r in averages_table([a]).rows if r[0] == "PO1")
    assert row[6] == "1.67" and row[7] == "2"


def test_level_distribution_separates_matrices_that_share_a_mean():
    """1+3 and 2+2 both average 2.00 and are not the same matrix."""
    spread = po_averages({"A": {"PO1": 1}, "B": {"PO1": 3}}, po_codes=["PO1"])[0]
    flat = po_averages({"A": {"PO1": 2}, "B": {"PO1": 2}}, po_codes=["PO1"])[0]
    assert spread.average_over_mapped == flat.average_over_mapped == 2.0
    assert spread.level_counts != flat.level_counts


def test_every_po_appears_even_when_nothing_maps_to_it():
    avgs = po_averages(LEVELS, po_codes=POS, co_codes=[c["code"] for c in COS])
    assert [a.po_code for a in avgs] == POS


def test_an_out_of_range_level_is_refused_not_averaged():
    with pytest.raises(ValueError, match="not 1, 2 or 3"):
        po_averages({"CO1": {"PO1": 4}}, po_codes=["PO1"])


def test_the_articulation_table_ends_with_the_average_row():
    t = articulation_table(LEVELS, po_codes=POS,
                           co_codes=[c["code"] for c in COS], course_code="BEC701")
    assert t.headers == ["CO"] + POS
    assert [r[0] for r in t.rows[:-1]] == ["CO1", "CO2", "CO3", "CO4"]
    last = t.rows[-1]
    assert last[0] == "BEC701 average"
    assert last[1] == "1.67"            # PO1
    assert last[POS.index("PO9") + 1] == "-"


def test_coverage_notes_name_the_empty_and_the_thin_columns():
    avgs = po_averages(LEVELS, po_codes=POS, co_codes=[c["code"] for c in COS])
    notes = " ".join(coverage_notes(avgs))
    assert "PO9" in notes                       # empty column, named
    assert "no course outcome" in notes


def test_a_single_cell_average_is_flagged_as_thin():
    lone = {"CO1": {"PO4": 3}, "CO2": {}, "CO3": {}}
    avgs = po_averages(lone, po_codes=POS, co_codes=["CO1", "CO2", "CO3"])
    assert any("single course outcome" in n and "PO4" in n
               for n in coverage_notes(avgs))


def test_the_document_carries_the_blooms_levels_behind_the_matrix():
    """
    A reader cannot check a mapping level without CL, and CL comes from K.
    An inferred K must be labelled as ours, not the university's.
    """
    tables, _ = build_copo_document(
        course_code="BEC701", course_title="VLSI Design", framework_code="GAPC_V4",
        po_codes=POS, po_titles=FW.po_titles, po_action_levels=FW.po_action_levels,
        cos=COS, co_po_levels=LEVELS)
    blooms = tables[2]
    assert blooms.headers == ["CO", "Statement", "Bloom's", "CL", "Level source"]
    by_co = {r[0]: r for r in blooms.rows}
    assert by_co["CO1"][2:] == ["K2", "1", "inferred from the verb"]
    assert by_co["CO3"][2:] == ["K6", "3", "declared by the syllabus"]


def test_the_method_note_states_which_denominator_is_which():
    _tables, notes = build_copo_document(
        course_code="X", course_title="", framework_code="GAPC_V4", po_codes=POS,
        po_titles=FW.po_titles, po_action_levels=FW.po_action_levels,
        cos=COS, co_po_levels=LEVELS)
    assert "Avg (mapped)" in notes[0] and "Avg (all COs)" in notes[0]
    assert "CL >= PL" in notes[0]


# ------------------------------------------------------------------- API

def _payload(**over):
    return {"framework_code": "GAPC_V4", "course_code": "BEC701",
            "course_title": "VLSI Design", "cos": COS,
            "co_po_levels": LEVELS, **over}


def test_api_averages_match_the_engine(client):
    body = client.post("/api/copo-averages", json=_payload()).json()
    by_po = {a["po_code"]: a for a in body["averages"]}
    assert by_po["PO1"]["average_over_mapped"] == 1.67
    assert by_po["PO1"]["average_over_all"] == 1.25
    assert by_po["PO9"]["average_over_mapped"] is None
    assert len(body["averages"]) == FW.po_count


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "csv", "md"])
def test_every_format_downloads_with_a_filename(client, fmt):
    r = client.post("/api/copo-document", json=_payload(fmt=fmt))
    assert r.status_code == 200, r.text
    assert len(r.content) > 200
    assert f'filename="BEC701_CO-PO_GAPC_V4.{fmt}"' in r.headers["content-disposition"]


def test_the_word_document_contains_the_average_row(client):
    docx = pytest.importorskip("docx")
    r = client.post("/api/copo-document", json=_payload(fmt="docx"))
    doc = docx.Document(io.BytesIO(r.content))
    matrix = doc.tables[1]
    assert [c.text for c in matrix.rows[0].cells] == ["CO"] + POS
    last = [c.text for c in matrix.rows[-1].cells]
    assert last[0] == "BEC701 average"
    assert last[1] == "1.67"
    # ...and the averages table spells out both denominators.
    heads = [c.text for c in doc.tables[2].rows[0].cells]
    assert "Avg (mapped)" in heads and "Avg (all COs)" in heads


def test_a_bad_po_code_is_refused_rather_than_averaged(client):
    r = client.post("/api/copo-document",
                    json=_payload(co_po_levels={"CO1": {"PO12": 2}}))
    assert r.status_code == 400
    assert "PO12" in r.json()["detail"]


def test_an_unknown_format_is_refused(client):
    r = client.post("/api/copo-document", json=_payload(fmt="pdf"))
    assert r.status_code == 400
    assert "docx" in r.json()["detail"]


def test_a_document_with_no_outcomes_is_refused(client):
    r = client.post("/api/copo-document", json=_payload(cos=[]))
    assert r.status_code == 400
