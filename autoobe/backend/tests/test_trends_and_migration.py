"""Multi-year trends, Criterion 8 closure, and the 12 -> 11 migration."""
from __future__ import annotations

import pytest

from app.engine import migration as M
from app.engine import trends as T


def _run(year, values, framework="GAPC_V4"):
    return {
        "academic_year": year,
        "created_at": f"{year}-06-01T00:00:00+00:00",
        "config": {"framework_code": framework},
        "result": {"po_direct_attainment": values},
    }


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

def test_series_are_built_per_outcome_across_years():
    runs = [
        _run("2023-24", {"PO1": 1.8, "PO2": 2.0}),
        _run("2024-25", {"PO1": 2.1, "PO2": 2.0}),
        _run("2025-26", {"PO1": 2.4, "PO2": 1.6}),
    ]
    series = {s.target_code: s for s in T.build_series(runs)}
    assert series["PO1"].values == [1.8, 2.1, 2.4]
    assert series["PO1"].direction == "improving"
    assert series["PO1"].change == pytest.approx(0.6)
    assert series["PO2"].direction == "declining"


def test_flat_series_is_not_called_improving():
    runs = [_run("2024-25", {"PO1": 2.0}), _run("2025-26", {"PO1": 2.05})]
    assert T.build_series(runs)[0].direction == "flat"


def test_single_year_is_insufficient_data_not_flat():
    runs = [_run("2025-26", {"PO1": 2.0})]
    assert T.build_series(runs)[0].direction == "insufficient-data"


def test_gap_is_measured_against_the_target():
    runs = [_run("2024-25", {"PO1": 2.0}), _run("2025-26", {"PO1": 2.1})]
    series = T.build_series(runs, targets={"PO1": 2.5})
    assert series[0].gap == pytest.approx(-0.4)
    assert series[0].meets_target is False


def test_legacy_years_are_aligned_through_the_crosswalk():
    """
    Old PO8 (Ethics) is new PO7. Plotting the ordinal across the boundary
    would splice Ethics onto Environment-and-Sustainability.
    """
    runs = [
        _run("2023-24", {"PO8": 2.2}, framework="GAPC_V3"),
        _run("2025-26", {"PO7": 2.6}),
    ]
    series = {s.target_code: s for s in T.build_series(runs)}
    assert series["PO7"].values == [2.2, 2.6]
    assert series["PO7"].direction == "improving"


def test_the_merged_pair_is_averaged_and_marked():
    """Old PO6 and PO7 both fold into new PO6 -- one point from two."""
    runs = [
        _run("2023-24", {"PO6": 2.0, "PO7": 3.0}, framework="GAPC_V3"),
        _run("2025-26", {"PO6": 2.5}),
    ]
    series = {s.target_code: s for s in T.build_series(runs)}
    po6 = series["PO6"]
    assert po6.points[0].value == pytest.approx(2.5)
    assert po6.points[0].merged_from == ("PO6", "PO7")
    assert po6.points[0].is_merged
    assert po6.has_merged_points
    assert not po6.points[1].is_merged


def test_merged_points_are_disclosed_in_the_findings():
    runs = [
        _run("2023-24", {"PO6": 2.0, "PO7": 3.0}, framework="GAPC_V3"),
        _run("2025-26", {"PO6": 2.5}),
    ]
    findings = T.gap_analysis(T.build_series(runs))
    assert any("predate the framework change" in f.message
               and "not point for point" in f.message for f in findings)


def test_v3_series_do_not_produce_a_twelfth_column():
    runs = [_run("2023-24", {f"PO{i}": 2.0 for i in range(1, 13)},
                 framework="GAPC_V3")]
    codes = {s.target_code for s in T.build_series(runs)}
    assert "PO12" not in codes
    assert len(codes) == 11


def test_outcome_with_no_data_is_an_error():
    runs = [_run("2025-26", {"PO1": 2.0})]
    findings = T.gap_analysis(T.build_series(runs))
    po2 = [f for f in findings if f.target_code == "PO2"]
    assert po2 and po2[0].severity == "error"
    assert "automatic finding" in po2[0].message


def test_below_target_and_declining_is_an_error():
    runs = [_run("2024-25", {"PO1": 2.4}), _run("2025-26", {"PO1": 1.9})]
    findings = T.gap_analysis(T.build_series(runs, targets={"PO1": 2.2}))
    f = next(f for f in findings if f.target_code == "PO1")
    assert f.severity == "error"
    assert "still declining" in f.message


def test_declining_but_still_above_target_is_a_warning():
    runs = [_run("2024-25", {"PO1": 2.9}), _run("2025-26", {"PO1": 2.4})]
    findings = T.gap_analysis(T.build_series(runs, targets={"PO1": 2.0}))
    f = next(f for f in findings if f.target_code == "PO1")
    assert f.severity == "warning"
    assert "most often missed" in f.message


def test_findings_are_ordered_worst_first():
    runs = [
        _run("2024-25", {"PO1": 2.4, "PO2": 1.0}),
        _run("2025-26", {"PO1": 2.6, "PO2": 0.9}),
    ]
    findings = T.gap_analysis(T.build_series(runs, targets={"PO1": 2.0, "PO2": 2.0}))
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s:
                                {"error": 0, "warning": 1, "info": 2}[s])


# --- Criterion 8 closure ---------------------------------------------------

def _series(values, code="PO3"):
    """Only the named outcome -- build_series returns all eleven, and two
    concatenated batches would let an empty PO3 overwrite a populated one."""
    runs = [_run(y, {code: v}) for y, v in values]
    return [s for s in T.build_series(runs) if s.target_code == code]


def test_a_working_action_is_reported_as_closed():
    series = _series([("2023-24", 1.8), ("2024-25", 2.3)])
    checks = T.check_closure(series, [{"target_code": "PO3",
                                       "action": "Added a design studio",
                                       "academic_year": "2023-24"}])
    assert checks[0].verdict == "closed"
    assert checks[0].delta == pytest.approx(0.5)
    assert "closure evidence" in checks[0].message


def test_an_action_that_did_nothing_is_reported_plainly():
    series = _series([("2023-24", 2.0), ("2024-25", 2.02)])
    checks = T.check_closure(series, [{"target_code": "PO3", "action": "Extra tutorial",
                                       "academic_year": "2023-24"}])
    assert checks[0].verdict == "no-effect"
    assert "did not move" in checks[0].message


def test_a_backfiring_action_is_reported_rather_than_hidden():
    series = _series([("2023-24", 2.4), ("2024-25", 1.9)])
    checks = T.check_closure(series, [{"target_code": "PO3", "action": "Changed the rubric",
                                       "academic_year": "2023-24"}])
    assert checks[0].verdict == "worsened"
    assert "documented failed action" in checks[0].message


def test_an_open_loop_is_pending_not_closed():
    series = _series([("2024-25", 2.0)])
    checks = T.check_closure(series, [{"target_code": "PO3", "action": "New lab",
                                       "academic_year": "2024-25"}])
    assert checks[0].verdict == "pending"
    assert "loop is open" in checks[0].message


def test_closure_lists_problems_before_successes():
    series = _series([("2023-24", 2.0), ("2024-25", 2.5)])
    series += _series([("2023-24", 2.4), ("2024-25", 1.8)], code="PO4")
    checks = T.check_closure(series, [
        {"target_code": "PO3", "action": "a", "academic_year": "2023-24"},
        {"target_code": "PO4", "action": "b", "academic_year": "2023-24"},
    ])
    assert checks[0].verdict == "worsened"
    assert checks[-1].verdict == "closed"


def test_summary_counts_the_headline_numbers():
    runs = [
        _run("2024-25", {"PO1": 2.4, "PO2": 1.4}),
        _run("2025-26", {"PO1": 2.6, "PO2": 0.9}),
    ]
    s = T.summarise(T.build_series(runs, targets={"PO1": 2.0, "PO2": 2.0}))
    assert s["years"] == ["2024-25", "2025-26"]
    assert s["meeting_target"] == 1 and s["below_target"] == 1
    assert s["improving"] == 1 and s["declining"] == 1
    assert s["crosses_framework_boundary"] is False


# ---------------------------------------------------------------------------
# 12 -> 11 migration
# ---------------------------------------------------------------------------

LEGACY = {
    "CO1": {"PO1": 3, "PO2": 2},
    "CO2": {"PO6": 2, "PO7": 3},          # the merge
    "CO3": {"PO8": 3, "PO12": 2},         # ethics + lifelong learning
}


def test_eleven_of_twelve_columns_carry_over_unchanged():
    r = M.migrate_matrix(LEGACY)
    assert r.levels["CO1"] == {"PO1": 3, "PO2": 2}
    assert r.levels["CO3"] == {"PO7": 3, "PO11": 2}, \
        "Old PO8 -> new PO7 and old PO12 -> new PO11."


def test_the_merged_pair_takes_the_higher_level():
    r = M.migrate_matrix(LEGACY)
    assert r.levels["CO2"] == {"PO6": 3}


def test_merged_cells_are_flagged_for_review():
    r = M.migrate_matrix(LEGACY)
    merged = r.merged_cells
    assert len(merged) == 1
    assert merged[0].sources == ("PO6", "PO7")
    assert merged[0].needs_review, "The two levels differed, so a human decides."
    assert "both merge into this outcome" in merged[0].message


def test_identical_merged_levels_need_no_review():
    r = M.migrate_matrix({"CO1": {"PO6": 2, "PO7": 2}})
    assert r.levels["CO1"] == {"PO6": 2}
    assert not r.merged_cells[0].needs_review


def test_zero_cells_are_not_migrated():
    r = M.migrate_matrix({"CO1": {"PO1": 0, "PO2": 3}})
    assert r.levels["CO1"] == {"PO2": 3}, "'-' must not become a mapping."


def test_unknown_legacy_column_is_dropped_with_a_warning():
    r = M.migrate_matrix({"CO1": {"PO13": 3}})
    assert r.levels["CO1"] == {}
    assert any("no equivalent" in w for w in r.warnings)


def test_migration_warns_that_column_means_move():
    r = M.migrate_matrix(LEGACY)
    assert any("column mean rises" in w for w in r.warnings)


def test_compare_produces_both_matrices_and_a_recommendation():
    cos = {
        "CO1": ("Explain the architecture of a database management system.", [2]),
        "CO2": ("Analyze the environmental and societal impact of engineering "
                "activity on public health.", [4]),
        "CO3": ("Apply ethical principles and professional responsibility to "
                "engineering practice.", [3]),
    }
    result = M.migrate_and_compare(LEGACY, cos)
    assert result.migrated.levels
    assert result.rederived
    assert 0.0 <= result.agreement_rate <= 1.0
    assert result.recommendation
    assert result.validation_migrated["findings"] is not None


def test_disagreements_explain_which_side_is_missing():
    cos = {"CO1": ("Explain the architecture of a database system.", [2])}
    result = M.migrate_and_compare({"CO1": {"PO5": 3}}, cos)
    msgs = " ".join(d.message for d in result.disagreements)
    assert "legacy matrix" in msgs


def test_recommendation_favours_rederiving_a_structurally_broken_matrix():
    """
    A legacy matrix leaving a CO mapped to nothing is an orphan CO, which is
    an automatic finding. Porting it forward ports the finding.
    """
    cos = {"CO1": ("Explain the architecture of a database system.", [2])}
    result = M.migrate_and_compare({"CO1": {}}, cos)
    assert result.recommendation.startswith("Re-derive")
    assert "ports the problem" in result.recommendation


def test_recommendation_favours_migrating_when_the_two_agree():
    """Migration preserves the department's own judgement and audit history."""
    cos = {"CO1": ("Explain the architecture of a database management system.", [2])}
    result = M.migrate_and_compare({"CO1": {"PO1": 2}}, cos)
    result.disagreements = []
    assert result.agreement_rate == 1.0
    assert result.recommendation.startswith("Migrate")
    assert "preserves the department" in result.recommendation
