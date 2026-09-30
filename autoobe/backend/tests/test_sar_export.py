"""SAR export shape tests -- column counts must follow the framework."""
from __future__ import annotations

from app.engine.sar_export import (
    course_articulation_matrix, po_direct_attainment_table, program_articulation_matrix,
)

CO_PO = {"CO1": {"PO1": 3, "PO2": 2}, "CO2": {"PO3": 1}}


class TestCourseArticulationMatrix:
    def test_v4_emits_eleven_po_columns(self):
        t = course_articulation_matrix(
            course_code="C1", course_name="Test", framework_code="GAPC_V4",
            co_codes=["CO1", "CO2"], co_po_levels=CO_PO,
        )
        assert t.number == "1.4.2.1"
        assert t.headers[1:] == [f"PO{i}" for i in range(1, 12)]
        assert len(t.rows) == 2

    def test_v3_emits_twelve_po_columns(self):
        t = course_articulation_matrix(
            course_code="C1", course_name="Test", framework_code="GAPC_V3",
            co_codes=["CO1"], co_po_levels=CO_PO,
        )
        assert t.headers[-1] == "PO12"
        assert len(t.headers) == 13

    def test_unmapped_cells_render_as_dash(self):
        t = course_articulation_matrix(
            course_code="C1", course_name="Test", framework_code="GAPC_V4",
            co_codes=["CO1"], co_po_levels=CO_PO,
        )
        row = t.rows[0]
        assert row[1] == "3"   # PO1
        assert row[2] == "2"   # PO2
        assert row[3] == "-"   # PO3 unmapped
        assert "0" not in row[1:]

    def test_pso_columns_appended(self):
        t = course_articulation_matrix(
            course_code="C1", course_name="Test", framework_code="GAPC_V4",
            co_codes=["CO1"], co_po_levels=CO_PO,
            pso_codes=["PSO1", "PSO2"], co_pso_levels={"CO1": {"PSO1": 3}},
        )
        assert t.headers[-2:] == ["PSO1", "PSO2"]
        assert t.rows[0][-2:] == ["3", "-"]


class TestDirectAttainmentTable:
    def test_includes_the_average_row(self):
        t = po_direct_attainment_table(
            framework_code="GAPC_V4", course_codes=["C1", "C2"],
            course_po_attainment={"C1": {"PO1": 2.5}, "C2": {"PO1": 1.5}},
            direct_attainment={"PO1": 2.0},
        )
        assert t.number == "3.8.1"
        assert t.rows[-1][0] == "Direct Attainment"
        assert t.rows[-1][1] == "2.00"

    def test_none_renders_as_dash_not_zero(self):
        t = po_direct_attainment_table(
            framework_code="GAPC_V4", course_codes=["C1"],
            course_po_attainment={"C1": {"PO1": 2.5, "PO2": None}},
            direct_attainment={"PO1": 2.5, "PO2": None},
        )
        assert t.rows[0][2] == "-"
        assert t.rows[-1][2] == "-"


def test_program_articulation_matrix_one_row_per_course():
    t = program_articulation_matrix(
        framework_code="GAPC_V4",
        courses=[("C1", "Course One"), ("C2", "Course Two")],
        course_po_levels={"C1": {"PO1": 2.5}, "C2": {"PO1": 1.0}},
    )
    assert t.number == "1.5.1"
    assert len(t.rows) == 2
    assert t.rows[0][:2] == ["C1", "Course One"]


class TestSerialisation:
    def _table(self):
        return course_articulation_matrix(
            course_code="C1", course_name="Test", framework_code="GAPC_V4",
            co_codes=["CO1"], co_po_levels=CO_PO,
        )

    def test_markdown_has_header_and_separator(self):
        md = self._table().to_markdown().splitlines()
        assert md[0].startswith("**Table No. 1.4.2.1:**")
        assert md[3].startswith("| ---")

    def test_csv_row_count_matches(self):
        csv = self._table().to_csv().splitlines()
        assert len(csv) == 2  # header + one CO

    def test_csv_quotes_fields_containing_commas(self):
        t = program_articulation_matrix(
            framework_code="GAPC_V4",
            courses=[("C1", "Signals, Systems and Networks")],
            course_po_levels={"C1": {"PO1": 2.0}},
        )
        assert '"Signals, Systems and Networks"' in t.to_csv()
