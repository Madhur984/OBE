"""
Departmental conventions, and the line between them and inference.

A convention is the department asserting something about its own programme.
An inference is the engine reading one outcome's text. Both are legitimate;
presenting one as the other in a document a peer team reads is not, and these
tests exist to make that impossible to do by accident.

The measured facts behind the two conventions shipped here, taken from the
department's own hand-built 2024-25 matrix (214 outcomes, 44 courses):

  * life-long learning is mapped for 92.1% of all outcomes, 191 of them at
    level 3 — the most heavily populated of all fourteen columns
  * PO1..PO5 and both PSOs are mapped for 85% of outcomes and for every
    outcome of every engineering course, while PO6..PO11 sit at 10-19%

The critical property, asserted below several ways: a convention sets
COMPETENCY, and Rule 1 / Rule 2 still decide the LEVEL. That is what makes it
reproduce a hand-built matrix rather than flatten it — PO3 comes out 3 for a
design outcome and 2 for an apply-level one, in the same column, on the same
convention.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.engine.conventions import (
    CORE, CORE_COLUMNS, LAB, LIFELONG, NON_TECHNICAL, PROJECT, THEORY,
    ConventionConfig, CoreConfig, apply_convention, apply_core_columns,
    classify_course, columns_for_course,
)
from app.engine.frameworks import get_framework, lifelong_po_code
from app.engine.mapping_rules import Competency, map_co_to_po


@pytest.fixture()
def client():
    return TestClient(app)


def test_the_lifelong_po_is_found_by_title_not_by_ordinal():
    """PO12 in GAPC v3, PO11 in v4, and the seeds do not even spell it alike."""
    assert lifelong_po_code("GAPC_V3") == "PO12"
    assert lifelong_po_code("GAPC_V4") == "PO11"
    # both carry PL 1, which is why Rule 2 returns 3 for every Bloom level
    for code in ("GAPC_V3", "GAPC_V4"):
        fw = get_framework(code)
        assert fw.po_action_levels[lifelong_po_code(code)] == 1


def test_nothing_is_applied_unless_asked():
    """The server default is off for both conventions."""
    assert ConventionConfig().enabled is False
    assert CoreConfig().enabled is False
    comp = {"CO1": {"PO1": 2}}
    r = apply_convention(comp, co_blooms={"CO1": [3]}, framework_code="GAPC_V3")
    assert r.cells == [] and r.competency == comp
    r2 = apply_core_columns(comp, co_blooms={"CO1": [3]},
                            valid_codes=get_framework("GAPC_V3").po_action_levels)
    assert r2.cells == [] and r2.competency == comp


def test_a_convention_sets_competency_and_the_rules_set_the_level():
    """
    The whole design in one assertion. The convention says "competency 2";
    Rule 2 turns that into 3 against PL 1 and into 2 against PL 3, from the
    outcome's own Bloom's level.
    """
    fw = get_framework("GAPC_V3")
    r = apply_core_columns(
        {}, co_blooms={"CO1": [3]},          # K3 -> CL 2
        valid_codes=fw.po_action_levels,
        config=CoreConfig(enabled=True, codes=("PO1", "PO3")))
    assert {c.po_code: c.competency for c in r.cells} == {"PO1": 2, "PO3": 2}
    # same competency, different level, because PO1 is PL 2 and PO3 is PL 3
    got = {po: map_co_to_po(po_code=po, competency=Competency.STRONG,
                            co_blooms_levels=[3],
                            po_action_level=fw.po_action_levels[po]).level
           for po in ("PO1", "PO3")}
    assert got == {"PO1": 3, "PO3": 2}


def test_the_input_is_never_mutated():
    comp = {"CO1": {"PO1": 2}}
    apply_convention(comp, co_blooms={"CO1": [3]}, framework_code="GAPC_V3",
                     config=ConventionConfig(enabled=True))
    assert comp == {"CO1": {"PO1": 2}}


def test_lifelong_never_overrides_what_the_engine_read():
    """
    The engine inferred a partial match against PO12 for CO2. That reading
    stands: this convention only fills what the engine left empty.
    """
    r = apply_convention({"CO1": {}, "CO2": {"PO12": 1}},
                         co_blooms={"CO1": [2], "CO2": [6]},
                         framework_code="GAPC_V3",
                         config=ConventionConfig(enabled=True))
    assert r.competency["CO2"]["PO12"] == 1
    assert [c.co_code for c in r.cells] == ["CO1"]
    assert r.cells[0].convention == LIFELONG
    assert r.cells[0].raised_from is None


def test_a_strong_match_steps_down_when_there_is_no_bloom_level():
    """
    Rule 2 has nothing to work on without a Bloom's level, and /api/map
    refuses the request. A convention must never be the thing that breaks a
    matrix, so it records a partial instead and says why.
    """
    r = apply_convention({}, co_blooms={"CO1": []}, framework_code="GAPC_V3",
                         config=ConventionConfig(enabled=True))
    assert r.cells[0].competency == 1
    assert "no Bloom's level" in r.cells[0].reason


def test_a_framework_without_the_po_degrades_rather_than_failing():
    class _Fake:
        po_codes = ("PO1",)
        po_titles = {"PO1": "Engineering Knowledge"}

    import app.engine.conventions as C

    real = C.lifelong_po_code
    C.lifelong_po_code = lambda code: None
    try:
        r = apply_convention({}, co_blooms={"CO1": [3]}, framework_code="X",
                             config=ConventionConfig(enabled=True))
        assert r.cells == []
    finally:
        C.lifelong_po_code = real


# ------------------------------------------------- the core-columns rule

def test_core_columns_fill_every_outcome():
    fw = get_framework("GAPC_V3")
    r = apply_core_columns({}, co_blooms={"CO1": [3], "CO2": [6]},
                           valid_codes=fw.po_action_levels,
                           config=CoreConfig(enabled=True))
    assert {c.po_code for c in r.cells} == set(CORE_COLUMNS)
    assert len(r.cells) == 2 * len(CORE_COLUMNS)
    assert all(c.convention == CORE for c in r.cells)
    # and nothing outside the named columns is touched
    assert "PO6" not in r.competency["CO1"]


def test_a_column_the_framework_lacks_is_skipped_silently():
    fw = get_framework("GAPC_V4")            # 11 POs, no PO12
    r = apply_core_columns({}, co_blooms={"CO1": [3]},
                           valid_codes=fw.po_action_levels,
                           config=CoreConfig(enabled=True,
                                             codes=("PO1", "PO12", "PSO1")))
    assert [c.po_code for c in r.cells] == ["PO1"]


def test_keeping_a_weaker_reading_is_the_default():
    r = apply_core_columns({"CO1": {"PO1": 1}}, co_blooms={"CO1": [3]},
                           valid_codes={"PO1": 2},
                           config=CoreConfig(enabled=True, codes=("PO1",)))
    assert r.competency["CO1"]["PO1"] == 1
    assert r.cells == []


def test_raising_a_weaker_reading_is_asked_for_and_reported():
    """
    Reproducing a hand-built matrix needs the department's assertion to beat
    a scorer's lexical "partial" — but never silently. The raised cell says
    what it was raised from.
    """
    r = apply_core_columns({"CO1": {"PO1": 1}}, co_blooms={"CO1": [3]},
                           valid_codes={"PO1": 2},
                           config=CoreConfig(enabled=True, codes=("PO1",),
                                             on_conflict="raise"))
    assert r.competency["CO1"]["PO1"] == 2
    assert len(r.cells) == 1
    assert r.cells[0].raised_from == 1
    assert "engine read only competency 1" in r.cells[0].reason


def test_a_convention_can_only_ever_raise():
    """A stronger reading by the engine is never pulled down to fit."""
    for policy in ("keep", "raise"):
        r = apply_core_columns({"CO1": {"PO1": 2}}, co_blooms={"CO1": [3]},
                               valid_codes={"PO1": 2},
                               config=CoreConfig(enabled=True, codes=("PO1",),
                                                 competency=1, on_conflict=policy))
        assert r.competency["CO1"]["PO1"] == 2
        assert r.cells == []


def test_a_bad_conflict_policy_is_refused():
    with pytest.raises(ValueError, match="on_conflict"):
        CoreConfig(on_conflict="overwrite")
    with pytest.raises(ValueError, match="competency"):
        CoreConfig(competency=3)


def test_the_conventions_are_off_the_inference_path():
    """
    `conventions.py` must not be reachable from the modules whose accuracy is
    measured, or the measurement stops meaning anything.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "engine"
    for name in ("competency.py", "mapping_rules.py", "copo_eval.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "conventions" not in text, f"{name} imports the convention module"
    text = (root / "conventions.py").read_text(encoding="utf-8")
    for name in ("competency", "mapping_rules", "copo_eval"):
        assert f"import {name}" not in text and f"engine.{name}" not in text


# ------------------------------------------------------------------- API

def test_the_api_reproduces_the_hand_built_matrix(client):
    """
    Digital System Design (BEC-302), against the department's own numbers.
    Every cell, including the ones that are not a 3.

    CO1's PO5 is 2 here where their sheet has 3 — see
    `test_po5_reads_as_a_moderate_contribution_not_a_substantial_one` in
    test_bloom_inference.py. It is the department's own later instruction,
    applied through Rule 2 rather than around it.
    """
    cols = ["PO1", "PO2", "PO3", "PO4", "PO5", "PO12", "PSO1", "PSO2"]
    manual = {
        "CO1": [3, 3, 2, 3, 2, 3, 3, 2],
        "CO2": [3, 3, 3, 3, 3, 3, 3, 3],
        "CO3": [3, 3, 3, 3, 3, 3, 3, 3],
        "CO4": [3, 3, 3, 3, 3, 3, 3, 3],
        "CO5": [3, 3, 3, 3, 3, 3, 3, 3],
    }
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3], "CO2": [4, 6], "CO3": [4, 6],
                      "CO4": [4, 6], "CO5": [6]},
        "competency": {},
        "psos": {
            "PSO1": "Apply the principles of electronics and communication "
                    "engineering to analyse and solve real-world problems.",
            "PSO2": "Design and develop embedded, VLSI and signal-processing "
                    "solutions using modern tools.",
        },
        "lifelong_convention": True, "core_convention": True,
        "core_codes": ["PO1", "PO2", "PO3", "PO4", "PO5", "PSO1", "PSO2"],
    }).json()
    for co, wanted in manual.items():
        got = [body["levels"][co].get(c, 0) for c in cols]
        assert got == wanted, f"{co}: {got} != {wanted}"
    # PSO PL comes from the department's own verb, not from a default
    assert body["action_levels"]["PSO1"] == 2   # "Apply …"
    assert body["action_levels"]["PSO2"] == 3   # "Design …"


def test_the_api_reports_every_convention_cell(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3]},
        "competency": {"CO1": {"PO1": 1}},
        "lifelong_convention": True, "core_convention": True,
        "core_codes": ["PO1", "PO2"], "core_on_conflict": "raise",
    }).json()
    by_po = {c["po_code"]: c for c in body["convention_cells"]}
    assert set(by_po) == {"PO1", "PO2", "PO12"}
    assert by_po["PO1"]["raised_from"] == 1     # the override is declared
    assert by_po["PO2"]["raised_from"] is None
    assert by_po["PO12"]["convention"] == LIFELONG


def test_conventions_are_off_by_default_over_the_api(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3]}, "competency": {"CO1": {"PO1": 2}},
    }).json()
    assert body["convention_cells"] == []
    assert body["levels"]["CO1"] == {"PO1": 3}


def test_a_pso_without_a_statement_is_not_a_column(client):
    r = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3]}, "competency": {"CO1": {"PSO1": 2}},
        "psos": {"PSO1": "   "},
    })
    assert r.status_code == 400
    assert "PSO1 is not a column" in r.json()["detail"]


# -------------------------------------------- which columns a course gets

def test_every_course_in_their_sheet_lands_on_the_right_half():
    """
    The classifier against all 44 blocks of the department's own workbook.

    "Which half" is not a judgement call in that file: six courses map
    PO6-PO11 and *zero* of PO1-PO5, and the other thirty-eight do the exact
    reverse. So the sheet itself says what the answer should be for every
    course, and this asserts all 44 rather than a sample.
    """
    import json
    from pathlib import Path

    gold = json.loads((Path(__file__).parent / "fixtures" /
                       "college_copo_2024-25.json").read_text(encoding="utf-8"))
    assert len(gold) == 44
    soft = [f"PO{i}" for i in range(6, 12)]
    hard = [f"PO{i}" for i in range(1, 6)]
    wrong = []
    for course in gold:
        rows = course["cos"]
        n_soft = sum(1 for r in rows for c in soft if r["levels"].get(c))
        n_hard = sum(1 for r in rows for c in hard if r["levels"].get(c))
        theirs = NON_TECHNICAL if n_soft > n_hard else "technical"
        ours = classify_course(title=course["course_title"],
                               code=course["course_code"])
        if (ours == NON_TECHNICAL) != (theirs == NON_TECHNICAL):
            wrong.append(f"{course['course_code']} {course['course_title']}: "
                         f"read as {ours}, their sheet says {theirs}")
    assert not wrong, "\n".join(wrong)


def test_the_four_shapes_are_told_apart():
    assert classify_course(title="Digital System Design") == THEORY
    assert classify_course(title="Digital System Design Lab") == LAB
    assert classify_course(title="Mini Project/ Internship") == PROJECT
    assert classify_course(title="Indian Tradition Culture and Society") == NON_TECHNICAL
    # a lab whose title does not say so: no lecture hours, practical hours
    assert classify_course(title="Electronic Devices", ltp=(0, 0, 2)) == LAB
    assert classify_course(title="Electronic Devices", ltp=(3, 1, 0)) == THEORY


def test_project_management_is_a_management_course_not_a_project():
    """
    The one title that reads both ways, and their sheet settles it: KHU-702
    "Project Management and Entrepreneurship" is mapped PO6-PO11 with PO1-PO5
    empty on every row. The non-technical test therefore runs first.
    """
    assert classify_course(title="Project Management and Entrepreneurship",
                           code="KHU-702") == NON_TECHNICAL
    assert classify_course(title="Project", code="KEC-753") == PROJECT


def test_maths_is_a_technical_course_despite_its_code():
    """
    BAS/KAS carries both Mathematics and Technical Communication, so the code
    prefix alone cannot decide. Their sheet maps Mathematics-III on PO1-PO5.
    """
    assert classify_course(title="MATHEMATICS-III", code="KAS-302") == THEORY
    assert classify_course(title="Technical Communication",
                           code="BAS-402") == NON_TECHNICAL


def test_the_psos_join_a_technical_course_and_not_a_humanities_one():
    """Again their practice: the six non-technical blocks leave both empty."""
    kind, cols = columns_for_course(title="Digital System Design",
                                    pso_codes=("PSO1", "PSO2"))
    assert kind == THEORY and cols[-2:] == ("PSO1", "PSO2")
    kind, cols = columns_for_course(title="Rural Development",
                                    pso_codes=("PSO1", "PSO2"))
    assert kind == NON_TECHNICAL and "PSO1" not in cols
    assert cols == ("PO6", "PO7", "PO8", "PO9", "PO10", "PO11")


def test_a_project_also_claims_communication():
    _, cols = columns_for_course(title="Mini Project/ Internship")
    assert "PO10" in cols            # the report and the viva
    _, theory = columns_for_course(title="Control System")
    assert "PO10" not in theory


def test_the_api_maps_a_humanities_course_on_the_other_half(client):
    """
    End to end: the same request that gives Digital System Design PO1-PO5
    gives Rural Development PO6-PO11, from the course title alone.
    """
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3]}, "competency": {},
        "course_title": "Rural Development", "course_code": "KHU-801",
        "lifelong_convention": True, "core_convention": True,
    }).json()
    assert body["course_kind"] == NON_TECHNICAL
    assert set(body["levels"]["CO1"]) == {"PO6", "PO7", "PO8", "PO9",
                                          "PO10", "PO11", "PO12"}
    assert "PO1" not in body["levels"]["CO1"]


def test_the_course_kind_is_reported_not_applied_silently(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [4]}, "competency": {},
        "course_title": "Digital System Design Lab",
        "lifelong_convention": True, "core_convention": True,
    }).json()
    assert body["course_kind"] == LAB
    assert body["course_kind_label"] == "laboratory course"
    assert body["core_codes"] == list(CORE_COLUMNS)


def test_without_a_course_the_explicit_codes_are_used(client):
    """The old contract still holds for a caller that names its own columns."""
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [4]}, "competency": {},
        "core_convention": True, "core_codes": ["PO2", "PO7"],
    }).json()
    assert body["course_kind"] == ""
    assert set(body["levels"]["CO1"]) == {"PO2", "PO7"}


# ------------------------------------- filling only what is really needed

def test_a_column_nothing_points_at_is_left_alone():
    """
    "It is not necessary to fill all the columns till PO5 — if they really
    needed to be filled then only fill them, else let them be as they are."

    With `require_evidence` the convention still says which columns this
    department maps, but each cell has to be able to point at a word. A
    column the outcome and its syllabus are both silent about stays as it
    was, which is usually blank.
    """
    ev = {"CO1": {"PO1": 1.5, "PO3": 3.1}}          # nothing for PO2, PO4, PO5
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 6)},
                           config=CoreConfig(enabled=True, require_evidence=True),
                           evidence=ev,
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 6)))
    assert {c.po_code for c in r.cells} == {"PO1", "PO3"}
    assert "PO4" not in r.competency.get("CO1", {})


def test_without_the_gate_every_named_column_is_still_filled():
    """The blanket is still reachable; it is simply no longer the default."""
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 6)},
                           config=CoreConfig(enabled=True),   # require_evidence off
                           evidence={"CO1": {}})
    assert {c.po_code for c in r.cells} == set(CORE_COLUMNS)


def test_a_column_the_scorer_cannot_score_is_never_vetoed():
    """
    A PSO is a statement the department wrote; there is no signal lexicon
    behind it, so nothing can ever evidence one. Reading "no scorer" as "no
    evidence" emptied both PSO columns outright the first time this ran.
    """
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={"PO1": 2, "PSO1": 2, "PSO2": 3},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             codes=("PO1", "PSO1", "PSO2")),
                           evidence={"CO1": {}},          # not even PO1
                           evidence_scope=frozenset({"PO1"}))
    assert {c.po_code for c in r.cells} == {"PSO1", "PSO2"}


# -------------------------------------- filling them anyway, at the floor

def test_an_unevidenced_core_column_is_filled_at_one_not_left_blank():
    """
    The department's second instruction, and it pulls the other way from the
    first: "for technical subjects make sure to fill PO1-PO5; if according to
    you they are least relatable then keep them 1, but fill them."

    Both are honoured, because they are answers to different questions. The
    gate still decides whether a cell is *earned* by the text. What changes is
    what an unearned cell says: not nothing, but 1. Their argument is a
    curriculum one -- an engineering course contributes something to
    engineering knowledge whether or not its five sentences say so, and a
    blank claims the opposite.
    """
    ev = {"CO1": {"PO1": 1.5, "PO3": 3.1}}          # nothing for PO2, PO4, PO5
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 6)},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             unevidenced="partial"),
                           evidence=ev,
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 6)))
    assert {c.po_code for c in r.cells} == set(CORE_COLUMNS)
    row = r.competency["CO1"]
    assert row["PO1"] == 2 and row["PO3"] == 2      # earned by the text
    assert row["PO2"] == row["PO4"] == row["PO5"] == 1   # filled at the floor


def test_a_floored_cell_is_reported_as_floored():
    """
    A 1 the curriculum argument put there is not the same cell as a 1 the
    outcome earned, and the matrix must never blur the two -- the same reason
    `raised_from` exists.
    """
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 6)},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             unevidenced="partial"),
                           evidence={"CO1": {"PO1": 2.0}},
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 6)))
    floored = {c.po_code for c in r.cells if c.floored}
    assert floored == {"PO2", "PO3", "PO4", "PO5"}
    assert not [c for c in r.cells if c.po_code == "PO1" and c.floored]
    reason = next(c.reason for c in r.cells if c.po_code == "PO4")
    assert "floor" in reason.lower()


def test_the_floor_is_only_ever_the_core_five():
    """
    PO10 rides along on a project block, and the department's own sheet fills
    it on roughly three rows in four -- so flooring it would invent a pattern
    they do not have. The floor stops at PO5 on purpose.
    """
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 11)},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             unevidenced="partial",
                                             codes=CORE_COLUMNS + ("PO10",)),
                           evidence={"CO1": {}},
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 11)))
    assert {c.po_code for c in r.cells} == set(CORE_COLUMNS)
    assert "PO10" not in r.competency.get("CO1", {})


def test_the_floor_never_reaches_a_non_technical_course():
    """
    A course on the Constitution of India is given PO6-PO11 as its columns, so
    there is no core column for the floor to touch. This is the guard that
    keeps "fill PO1-PO5" from meaning "fill PO1-PO5 everywhere" -- which is
    the exact pattern that put engineering knowledge against rural development.
    """
    _, cols = columns_for_course(title="Constitution of India, Law and Engineering",
                                 code="BNC-501")
    r = apply_core_columns({}, co_blooms={"CO1": [2]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 13)},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             unevidenced="partial", codes=cols),
                           evidence={"CO1": {}},
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 13)))
    assert not (set(r.competency.get("CO1", {})) & set(CORE_COLUMNS))


def test_the_floor_is_off_unless_asked_for():
    """Same discipline as every other convention in this module."""
    r = apply_core_columns({}, co_blooms={"CO1": [6]},
                           valid_codes={f"PO{i}": 2 for i in range(1, 6)},
                           config=CoreConfig(enabled=True, require_evidence=True),
                           evidence={"CO1": {"PO1": 2.0}},
                           evidence_scope=frozenset(f"PO{i}" for i in range(1, 6)))
    assert {c.po_code for c in r.cells} == {"PO1"}
    with pytest.raises(ValueError, match="unevidenced"):
        CoreConfig(enabled=True, unevidenced="sometimes")


def test_an_existing_reading_survives_the_gate():
    """`require_evidence` only ever withholds a NEW cell. It never removes."""
    r = apply_core_columns({"CO1": {"PO4": 1}}, co_blooms={"CO1": [6]},
                           valid_codes={"PO4": 2},
                           config=CoreConfig(enabled=True, require_evidence=True,
                                             codes=("PO4",), on_conflict="raise"),
                           evidence={"CO1": {}}, evidence_scope=frozenset({"PO4"}))
    assert r.competency["CO1"]["PO4"] == 2      # raised, not dropped


def test_digital_system_design_keeps_tool_usage_and_loses_investigation(client):
    """
    The case the gate exists for, on the course the department calls their
    standard. Unit IV of BEC-302 names Verilog RTL and three EDA tools by
    vendor, so PO5 is evidenced for every outcome. Nothing anywhere in the
    course speaks of experiments or investigation, so PO4 is left blank —
    a lecture course does not conduct investigations, and the paired lab is
    where that belongs.
    """
    cos = {
        "CO1": "Perform numerous arithmetic and logic simplification using various methods.",
        "CO2": "Design and analyze modular combinational circuits with MUX / DEMUX.",
    }
    unit4 = ("Logic families and semiconductor memories: TTL NAND gate, concept of "
             "programmable logic devices such as FPGA, Verilog RTL coding of "
             "combinational and sequential elements, and logic implementation using "
             "programmable devices (Siemens EDA Precision RTL / Cadence Genus).")
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3], "CO2": [4, 6]}, "competency": {},
        "co_statements": cos, "syllabus": unit4,
        "course_title": "Digital System Design", "course_code": "BEC302",
        "core_convention": True, "core_require_evidence": True,
        "core_on_conflict": "raise", "lifelong_convention": True,
    }).json()
    assert body["core_evidence_used"] is True
    for co in cos:
        mapped = body["levels"][co]
        assert "PO5" in mapped, f"{co} lost tool usage despite the EDA units"
        assert "PO4" not in mapped, f"{co} claims investigation with no evidence"
        assert "PO1" in mapped and "PO12" in mapped


def test_a_named_discipline_in_front_of_project_management_is_technical():
    """
    "project management" is in the non-technical title pattern because the
    department's KHU-702 belongs there. AKTU also publishes BOE068 and KIT071
    *Software* Project Management, which is a computer-science elective --
    estimation models, risk analysis, configuration-management tools -- and it
    was being filed alongside Rural Development and handed PO6-PO11.

    The guard for it existed in this module and was never called.
    """
    assert classify_course(title="Software Project Management") == THEORY
    assert classify_course(title="Construction Project Management") == THEORY
    # and the qualifier is what does it -- the bare title is unchanged
    assert classify_course(title="Project Management") == NON_TECHNICAL
    assert classify_course(
        title="Project Management and Entrepreneurship") == NON_TECHNICAL
    # the negative lookahead that keeps it out of the project bucket, too
    assert classify_course(title="Mini Project") == PROJECT


def test_a_technical_course_comes_back_with_all_five_core_columns(client):
    """
    The department's instruction end to end. Digital System Design has no
    outcome that investigates anything -- PO4 was blank, and that read as
    "this course does not contribute to investigation", which is a stronger
    claim than they want to make. It now comes back at 1, and comes back
    marked as a floor cell so nobody mistakes it for an earned one.
    """
    cos = {
        "CO1": "Perform numerous arithmetic and logic simplification using various methods.",
        "CO2": "Design and analyze modular combinational circuits with MUX / DEMUX.",
    }
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3], "CO2": [4, 6]}, "competency": {},
        "co_statements": cos,
        "course_title": "Digital System Design", "course_code": "BEC302",
        "core_convention": True, "core_require_evidence": True,
        "core_unevidenced": "partial",
        "core_on_conflict": "raise", "lifelong_convention": True,
    }).json()
    for co in cos:
        mapped = body["levels"][co]
        for po in ("PO1", "PO2", "PO3", "PO4", "PO5"):
            assert po in mapped, f"{co} left {po} blank on a technical course"
            assert mapped[po] >= 1
    floored = {(c["co_code"], c["po_code"]) for c in body["convention_cells"]
               if c.get("floored")}
    assert ("CO1", "PO4") in floored          # filled by the rule, not the text
    assert all(body["levels"][co]["PO4"] == 1 for co in cos)


def test_the_gate_needs_the_statements_and_says_when_it_had_none(client):
    body = client.post("/api/map", json={
        "framework_code": "GAPC_V3",
        "co_blooms": {"CO1": [3]}, "competency": {},
        "course_title": "Digital System Design",
        "core_convention": True, "core_require_evidence": True,
    }).json()
    assert body["core_evidence_used"] is False       # nothing to weigh
    assert "PO4" in body["levels"]["CO1"]            # so it fell back to asserting
