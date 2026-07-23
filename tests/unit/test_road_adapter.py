from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
from shapely import LineString, Point, to_wkb

from conftest import make_road_canonical_fixture
from scene.roads.adapter import RoadAdapter
from scene.roads.reader import RoadReader
from scene.roads.validator import RoadValidator


def _read(tmp_path: Path, schema_path: Path):
    manifest, schema = make_road_canonical_fixture(tmp_path, schema_path)
    return schema, RoadReader(schema, tmp_path / "outputs").read(manifest)


def test_road_adapter_builds_two_unjoined_datasets(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, road_input = _read(tmp_path, canonical_schema_path)
    result = RoadAdapter(RoadValidator(schema)).adapt(road_input)
    assert result.validation.valid
    assert result.links.feature_count == 1
    assert result.nodes.feature_count == 1
    assert result.links.source_metadata.source_name == "seoul_roads_links"
    assert result.nodes.source_metadata.source_name == "seoul_roads_nodes"
    assert result.links.provenance_metadata.frame_name == "road_link"
    assert result.nodes.provenance_metadata.frame_name == "road_node"
    assert "geometry_wkb" not in result.links.attribute_dataframe.column_names
    assert "road_type" in result.links.attribute_dataframe.column_names
    assert "source_road_name" in result.links.attribute_dataframe.column_names
    assert "bridge" not in result.links.attribute_dataframe.column_names
    assert "tunnel" not in result.links.attribute_dataframe.column_names
    assert "direction" not in result.links.attribute_dataframe.column_names
    assert result.validation.topology_created is False
    assert result.validation.stable_id_created is False


def test_link_geometry_validation_rejects_point(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, road_input = _read(tmp_path, canonical_schema_path)
    index = road_input.link_table.schema.get_field_index("geometry_wkb")
    table = road_input.link_table.set_column(
        index,
        road_input.link_table.schema.field(index),
        pa.array([to_wkb(Point(0, 0))], type=pa.binary()),
    )
    result = RoadValidator(schema).validate(
        replace(road_input, link_table=table)
    )
    assert not result.valid
    assert result.link_geometry.unexpected_type_count == 1


def test_node_geometry_validation_rejects_line(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, road_input = _read(tmp_path, canonical_schema_path)
    index = road_input.node_table.schema.get_field_index("geometry_wkb")
    table = road_input.node_table.set_column(
        index,
        road_input.node_table.schema.field(index),
        pa.array([to_wkb(LineString([(0, 0), (1, 1)]))], type=pa.binary()),
    )
    result = RoadValidator(schema).validate(
        replace(road_input, node_table=table)
    )
    assert not result.valid
    assert result.node_geometry.unexpected_type_count == 1


def test_road_crs_validation(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, road_input = _read(tmp_path, canonical_schema_path)
    result = RoadValidator(schema).validate(
        replace(road_input, node_crs="EPSG:4326")
    )
    assert not result.crs_valid
    assert any(
        issue.code == "spatial_metadata_mismatch"
        for issue in result.issues
    )


def test_road_nullable_and_schema_validation(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    schema, road_input = _read(tmp_path, canonical_schema_path)
    index = road_input.link_table.schema.get_field_index("road_type")
    field = road_input.link_table.schema.field(index)
    table = road_input.link_table.set_column(
        index,
        field,
        pa.array([None], type=pa.string()),
    )
    result = RoadValidator(schema).validate(
        replace(road_input, link_table=table)
    )
    assert not result.link_attributes_valid
    assert any(
        issue.code == "attribute_nullability_violation"
        for issue in result.issues
    )
