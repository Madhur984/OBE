"""
Phase 0 exit-gate tests: the dual-framework requirement.

AutoOBE_Implementation_Plan.pdf Section 3.4 acceptance criterion -- both
frameworks must coexist, each computing correctly against its own 12 or 11
columns, with the crosswalk available as a reporting view rather than a
destructive transformation.
"""
from __future__ import annotations

import pytest

from app.engine.frameworks import FRAMEWORKS, get_framework, migrate_po_code


def test_both_frameworks_registered():
    assert set(FRAMEWORKS) == {"GAPC_V3", "GAPC_V4"}


def test_v3_has_twelve_pos():
    fw = get_framework("GAPC_V3")
    assert fw.po_count == 12
    assert fw.po_codes[-1] == "PO12"
    assert fw.po_titles["PO12"] == "Life-long Learning"


def test_v4_has_eleven_pos_and_nine_wks():
    fw = get_framework("GAPC_V4")
    assert fw.po_count == 11
    assert fw.po_codes[-1] == "PO11"
    assert fw.po_titles["PO11"] == "Life-Long Learning"
    assert len(fw.wk_statements) == 9


def test_v4_po6_is_the_merged_engineer_and_the_world():
    fw = get_framework("GAPC_V4")
    assert fw.po_titles["PO6"] == "The Engineer and The World"
    # v3 kept these as two separate outcomes.
    v3 = get_framework("GAPC_V3")
    assert v3.po_titles["PO6"] == "The Engineer and Society"
    assert v3.po_titles["PO7"] == "Environment and Sustainability"


def test_wk_linkage_matches_annexure_ii():
    fw = get_framework("GAPC_V4")
    assert fw.po_wk_refs["PO3"] == ("WK5",)
    assert fw.po_wk_refs["PO4"] == ("WK8",)
    assert fw.po_wk_refs["PO7"] == ("WK9",)
    assert fw.po_wk_refs["PO6"] == ("WK1", "WK5", "WK7")
    # PO8-PO10 carry no WK reference in the official text.
    assert fw.po_wk_refs["PO8"] == ()


def test_po_statements_are_never_empty():
    for fw in FRAMEWORKS.values():
        for code in fw.po_codes:
            assert fw.po_statements[code].strip(), f"{fw.code}/{code} has no statement"


class TestCrosswalk:
    def test_first_five_carry_forward_one_to_one(self):
        for i in range(1, 6):
            code = f"PO{i}"
            assert migrate_po_code(code, from_code="GAPC_V3", to_code="GAPC_V4") == (code,)

    def test_old_po6_and_po7_both_land_on_new_po6(self):
        assert migrate_po_code("PO6", from_code="GAPC_V3", to_code="GAPC_V4") == ("PO6",)
        assert migrate_po_code("PO7", from_code="GAPC_V3", to_code="GAPC_V4") == ("PO6",)

    def test_po8_through_po12_shift_down_one_slot(self):
        expected = {"PO8": "PO7", "PO9": "PO8", "PO10": "PO9", "PO11": "PO10", "PO12": "PO11"}
        for old, new in expected.items():
            assert migrate_po_code(old, from_code="GAPC_V3", to_code="GAPC_V4") == (new,)

    def test_reverse_crosswalk_surfaces_the_merge(self):
        # New PO6 has two v3 ancestors -- migrating back must not lose either.
        assert set(migrate_po_code("PO6", from_code="GAPC_V4", to_code="GAPC_V3")) == {"PO6", "PO7"}

    def test_same_framework_is_identity(self):
        assert migrate_po_code("PO9", from_code="GAPC_V4", to_code="GAPC_V4") == ("PO9",)

    def test_every_v3_po_maps_somewhere(self):
        v3 = get_framework("GAPC_V3")
        for code in v3.po_codes:
            assert migrate_po_code(code, from_code="GAPC_V3", to_code="GAPC_V4")

    def test_every_v4_po_has_an_ancestor(self):
        v4 = get_framework("GAPC_V4")
        for code in v4.po_codes:
            assert migrate_po_code(code, from_code="GAPC_V4", to_code="GAPC_V3")

    def test_invalid_codes_rejected(self):
        with pytest.raises(ValueError):
            migrate_po_code("PO12", from_code="GAPC_V4", to_code="GAPC_V3")  # v4 has no PO12
        with pytest.raises(ValueError):
            migrate_po_code("PO99", from_code="GAPC_V3", to_code="GAPC_V4")


def test_unknown_framework_rejected():
    with pytest.raises(ValueError, match="Unknown framework"):
        get_framework("GAPC_V5")
