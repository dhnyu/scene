from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
from shapely import Point, to_wkb

from conftest import make_building_canonical_fixture
from scene.buildings.adapter import BuildingAdapter
from scene.buildings.reader import BuildingReader
from scene.buildings.validator import BuildingValidator


def _read_fixture(tmp_path: Path):
    project = Path(__file__).resolve().parents[2]
    manifest, schema = make_building_canonical_fixture(
        tmp_path,
        project / "docs" / "contracts" / "canonical_schema.yaml",
    )
    canonical = BuildingReader(schema, tmp_path / "outputs").read(manifest)
    return schema, canonical


def test_building_adapter_creates_unjoined_dataset(tmp_path: Path) -> None:
    schema, canonical = _read_fixture(tmp_path)

    result = BuildingAdapter(BuildingValidator(schema)).adapt(canonical)

    assert result.validation.valid
    assert result.dataset.feature_count == 1
    assert result.dataset.attribute_row_count == 1
    assert result.dataset.crs == "EPSG:5186"
    assert result.dataset.bounding_box == (
        200000.0,
        550000.0,
        200010.0,
        550010.0,
    )
    assert result.dataset.geometry_dataframe is canonical.geometry_table
    assert result.dataset.attribute_dataframe is canonical.attribute_table
    assert result.validation.modalities_unjoined
    assert "stable_id" not in result.dataset.geometry_dataframe.column_names
    assert "observed_area_m2" not in (
        result.dataset.attribute_dataframe.column_names
    )


def test_geometry_validator_reports_unexpected_geometry_type(
    tmp_path: Path,
) -> None:
    schema, canonical = _read_fixture(tmp_path)
    point_wkb = pa.chunked_array(
        [[to_wkb(Point(200000.0, 550000.0))]],
        type=pa.binary(),
    )
    geometry = canonical.geometry_table.set_column(
        canonical.geometry_table.schema.get_field_index("geometry_wkb"),
        canonical.geometry_table.schema.field("geometry_wkb"),
        point_wkb,
    )

    result = BuildingValidator(schema).validate(
        replace(canonical, geometry_table=geometry)
    )

    assert not result.valid
    assert result.unexpected_geometry_type_count == 1
    assert any(
        issue.code == "unexpected_geometry_type"
        for issue in result.issues
    )


def test_attribute_validator_allows_declared_nullable_values(
    tmp_path: Path,
) -> None:
    schema, canonical = _read_fixture(tmp_path)
    attributes = canonical.attribute_table
    for name in (
        "building_use",
        "building_structure",
        "source_building_area_m2",
        "building_height_m",
    ):
        attributes = attributes.set_column(
            attributes.schema.get_field_index(name),
            attributes.schema.field(name),
            pa.chunked_array([[None]], type=attributes.schema.field(name).type),
        )

    result = BuildingValidator(schema).validate(
        replace(canonical, attribute_table=attributes)
    )

    assert result.valid
    assert result.attributes_valid


def test_crs_validator_rejects_noncanonical_crs(tmp_path: Path) -> None:
    schema, canonical = _read_fixture(tmp_path)

    result = BuildingValidator(schema).validate(
        replace(canonical, geometry_crs="EPSG:4326")
    )

    assert not result.valid
    assert not result.crs_valid


def test_building_attribute_mapping_remains_canonical(tmp_path: Path) -> None:
    schema, canonical = _read_fixture(tmp_path)

    result = BuildingAdapter(BuildingValidator(schema)).adapt(canonical)
    row = result.dataset.attribute_dataframe.to_pylist()[0]

    assert row["building_use"] == "use"
    assert row["building_structure"] == "structure"
    assert row["building_height_m"] == 12.0
    assert row["source_building_area_m2"] == 100.0
    assert "observed_area_m2" not in row
