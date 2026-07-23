from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio

from conftest import make_road_canonical_fixture
from scene.roads.adapter import RoadAdapter
from scene.roads.reader import RoadReader
from scene.roads.serialization import RoadSerializer
from scene.roads.validator import RoadValidator


def _codecs(path: Path) -> set[str]:
    parquet = pq.ParquetFile(path)
    return {
        parquet.metadata.row_group(row_group).column(column).compression
        for row_group in range(parquet.metadata.num_row_groups)
        for column in range(parquet.metadata.num_columns)
    }


def test_road_dataset_serialization(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_road_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    canonical = RoadReader(schema, tmp_path / "outputs").read(manifest)
    result = RoadAdapter(RoadValidator(schema)).adapt(canonical)
    artifacts = RoadSerializer().serialize(
        result.links,
        result.nodes,
        result.validation,
        tmp_path / "serialized",
        run_id="20260724_030000_KST",
    )

    layers = {name for name, _ in pyogrio.list_layers(artifacts.geometry_geopackage)}
    assert layers == {"road_links", "road_nodes"}
    link_info = pyogrio.read_info(
        artifacts.geometry_geopackage, layer="road_links"
    )
    node_info = pyogrio.read_info(
        artifacts.geometry_geopackage, layer="road_nodes"
    )
    assert (link_info["features"], link_info["geometry_type"]) == (
        1,
        "LineString",
    )
    assert (node_info["features"], node_info["geometry_type"]) == (1, "Point")
    assert link_info["crs"] == node_info["crs"] == "EPSG:5186"
    assert _codecs(artifacts.link_attribute_parquet) == {"ZSTD"}
    assert _codecs(artifacts.node_attribute_parquet) == {"ZSTD"}

    metadata = json.loads(artifacts.metadata_json.read_text(encoding="utf-8"))
    assert metadata["geometry_attributes_joined"] is False
    assert metadata["link_node_connected"] is False
    assert metadata["topology_created"] is False
    assert metadata["stable_id_created"] is False
    assert metadata["validation"]["valid"] is True
    availability = metadata["canonical_field_availability"]
    assert availability["road_class"] == {
        "canonical_column": "road_type",
        "status": "available",
    }
    assert availability["road_name"]["canonical_column"] == "source_road_name"
    assert availability["bridge"] == {
        "canonical_column": None,
        "status": "not_declared_in_canonical_schema",
    }
