from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
from shapely import LineString, Point, to_wkb

from conftest import make_poi_canonical_fixture
from scene.pois.adapter import POIAdapter
from scene.pois.category import CATEGORY_COLUMNS, CATEGORY_PATH_COLUMN
from scene.pois.reader import POIReader
from scene.pois.validator import POIValidator


def _read(tmp_path: Path, schema_path: Path):
    manifest, schema = make_poi_canonical_fixture(tmp_path, schema_path)
    return schema, POIReader(schema, tmp_path / "outputs").read(manifest)


def test_poi_adapter_builds_unjoined_dataset(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    result = POIAdapter(POIValidator(schema)).adapt(poi_input)
    assert result.validation.valid
    assert result.dataset.feature_count == 2
    assert result.dataset.attribute_row_count == 2
    assert (
        CATEGORY_PATH_COLUMN
        in result.dataset.attribute_dataframe.column_names
    )
    assert result.dataset.geometry_dataframe.num_columns == 6
    assert result.dataset.source_join_key_metadata.valid
    assert result.dataset.source_metadata["geometry"].source_name == (
        "seoul_poi_geometry"
    )
    assert result.dataset.provenance_metadata["attributes"].frame_name == (
        "poi_attribute"
    )
    for column in CATEGORY_COLUMNS:
        assert result.dataset.attribute_dataframe[column].equals(
            poi_input.attribute_table[column]
        )


def test_poi_geometry_validation_rejects_line_and_empty(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.geometry_table.schema.get_field_index("geometry_wkb")
    field = poi_input.geometry_table.schema.field(index)
    table = poi_input.geometry_table.set_column(
        index,
        field,
        pa.array(
            [
                to_wkb(LineString([(0, 0), (1, 1)])),
                to_wkb(Point()),
            ],
            type=pa.binary(),
        ),
    )
    validation = POIValidator(schema).validate(
        replace(poi_input, geometry_table=table)
    )
    assert not validation.geometry_valid
    assert validation.unexpected_geometry_type_count == 1
    assert validation.empty_geometry_count == 1


def test_poi_geometry_parse_and_crs_validation(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.geometry_table.schema.get_field_index("geometry_wkb")
    field = poi_input.geometry_table.schema.field(index)
    table = poi_input.geometry_table.set_column(
        index,
        field,
        pa.array([b"invalid", to_wkb(Point(0, 0))], type=pa.binary()),
    )
    validation = POIValidator(schema).validate(
        replace(
            poi_input,
            geometry_table=table,
            geometry_crs="EPSG:4326",
        )
    )
    assert validation.geometry_parse_failure_count == 1
    assert not validation.crs_valid


def test_join_key_duplicate_and_only_key_diagnostics(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.geometry_table.schema.get_field_index("source_poi_id")
    field = poi_input.geometry_table.schema.field(index)
    table = poi_input.geometry_table.set_column(
        index,
        field,
        pa.array(["poi-1", "poi-1"], type=pa.string()),
    )
    validation = POIValidator(schema).validate(
        replace(poi_input, geometry_table=table)
    )
    join = validation.join_key
    assert not join.valid
    assert join.geometry_duplicate_key_count == 1
    assert join.geometry_duplicate_row_count == 1
    assert join.attribute_only_key_count == 1
    assert join.geometry_only_key_count == 0
    assert join.cardinality == "many_to_one"


def test_join_key_null_diagnostics(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.attribute_table.schema.get_field_index("source_poi_id")
    field = poi_input.attribute_table.schema.field(index)
    table = poi_input.attribute_table.set_column(
        index,
        field,
        pa.array(["poi-1", None], type=pa.string()),
    )
    validation = POIValidator(schema).validate(
        replace(poi_input, attribute_table=table)
    )
    assert not validation.join_key.valid
    assert validation.join_key.attribute_null_key_count == 1
    assert validation.join_key.geometry_only_key_count == 1


def test_attribute_nullability_and_category_path_are_distinct(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.attribute_table.schema.get_field_index("poi_category_3")
    field = poi_input.attribute_table.schema.field(index)
    table = poi_input.attribute_table.set_column(
        index,
        field,
        pa.array([None, ""], type=pa.string()),
    )
    validation = POIValidator(schema).validate(
        replace(poi_input, attribute_table=table)
    )
    assert not validation.attributes_valid
    assert validation.category_path_valid
    assert validation.category_null_counts[2] == 1
    assert validation.category_empty_counts[2] == 1


def test_category_hierarchy_missing_field_is_reported(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, poi_input = _read(tmp_path, canonical_schema_path)
    index = poi_input.attribute_table.schema.get_field_index("poi_category_6")
    table = poi_input.attribute_table.remove_column(index)
    validation = POIValidator(schema).validate(
        replace(poi_input, attribute_table=table)
    )
    assert not validation.category_hierarchy_valid
    assert not validation.category_path_valid
    assert any(
        issue.code == "category_hierarchy_missing"
        for issue in validation.issues
    )
