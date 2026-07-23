from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from scene.schema.exceptions import SourceMappingError
from scene.schema.mapper import map_record_batch, map_table
from scene.schema.schema import load_canonical_schema


def _schema():
    root = Path(__file__).resolve().parents[2]
    return load_canonical_schema(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    )


def _inventory(source_name: str) -> dict[str, object]:
    return {
        "source_name": source_name,
        "source_path": f"/read-only/{source_name}",
        "sha256": "a" * 64,
        "crs": "EPSG:5186",
        "raster_width": 4,
        "raster_height": 3,
        "resolution_x": 5.0,
        "resolution_y": 5.0,
        "extent_min_x": 100.0,
        "extent_min_y": 200.0,
        "extent_max_x": 120.0,
        "extent_max_y": 215.0,
        "band_count": 1,
        "dtype": "Byte",
        "nodata": "0",
    }


def test_building_mapping_preserves_a12_as_provenance_only() -> None:
    schema = _schema()
    spec = schema.frame_for("seoul_buildings_attributes")
    table = pa.table(
        {
            "building_id": ["b-1"],
            "A9": ["use"],
            "A11": ["structure"],
            "A12": [123.5],
            "A16": [18.0],
            "source_feature_index": pa.array([7], type=pa.int32()),
            "source_zip": ["archive.zip"],
            "source_layer": ["building"],
            "processed_at": ["2026-07-23"],
            "target_epsg": pa.array([5186], type=pa.int32()),
            "geometry_layer": ["seoul_buildings_vworld"],
        }
    )

    frame = map_table(
        table,
        spec,
        _inventory(spec.source_name),
        schema_version=schema.schema_version,
        row_group_id=2,
        row_offset=10,
    )

    row = frame.table.to_pylist()[0]
    assert row["source_building_id"] == "b-1"
    assert row["building_use"] == "use"
    assert row["building_structure"] == "structure"
    assert row["building_height_m"] == 18.0
    assert row["source_building_area_m2"] == 123.5
    assert "observed_area_m2" not in frame.table.column_names
    assert "model_area" not in frame.table.column_names
    assert row["row_group_id"] == 2
    assert row["row_offset_within_group"] == 10


def test_road_mapping_casts_integral_lanes_and_keeps_native_links() -> None:
    schema = _schema()
    spec = schema.frame_for("seoul_roads_links")
    source = pa.RecordBatch.from_pydict(
        {
            "fid": pa.array([4], type=pa.int64()),
            "LINK_ID": ["l-1"],
            "F_NODE": ["n-1"],
            "T_NODE": ["n-2"],
            "LANES": [3.0],
            "ROAD_RANK": ["rank"],
            "ROAD_TYPE": ["type"],
            "ROAD_NO": ["101"],
            "ROAD_NAME": ["road"],
            "LENGTH": [42.5],
            "geom": [b"wkb"],
        }
    )

    mapped = map_record_batch(
        source,
        spec,
        _inventory(spec.source_name),
        schema_version=schema.schema_version,
    )
    row = mapped.to_pylist()[0]

    assert row["source_link_id"] == "l-1"
    assert row["from_source_node_id"] == "n-1"
    assert row["to_source_node_id"] == "n-2"
    assert row["lanes"] == 3
    assert mapped.schema.field("lanes").type == pa.int16()
    assert "parent_way_id" not in mapped.schema.names


def test_road_mapping_rejects_fractional_lanes() -> None:
    schema = _schema()
    spec = schema.frame_for("seoul_roads_links")
    source = pa.RecordBatch.from_pydict(
        {
            "fid": pa.array([4], type=pa.int64()),
            "LINK_ID": ["l-1"],
            "F_NODE": ["n-1"],
            "T_NODE": ["n-2"],
            "LANES": [1.5],
            "ROAD_RANK": ["rank"],
            "ROAD_TYPE": ["type"],
            "ROAD_NO": ["101"],
            "ROAD_NAME": ["road"],
            "LENGTH": [42.5],
            "geom": [b"wkb"],
        }
    )

    with pytest.raises(SourceMappingError, match="cannot safely map"):
        map_record_batch(
            source,
            spec,
            _inventory(spec.source_name),
            schema_version=schema.schema_version,
        )


def test_poi_six_level_mapping() -> None:
    schema = _schema()
    spec = schema.frame_for("seoul_poi_attributes")
    values: dict[str, pa.Array | list[str]] = {"NF_ID": ["p-1"]}
    for level in range(1, 7):
        values[f"POI_CL_DC_{level}"] = [f"c{level}"]
    table = pa.table(values)

    frame = map_table(
        table,
        spec,
        _inventory(spec.source_name),
        schema_version=schema.schema_version,
        row_group_id=0,
        row_offset=0,
    )

    row = frame.table.to_pylist()[0]
    assert row["source_poi_id"] == "p-1"
    assert [row[f"poi_category_{level}"] for level in range(1, 7)] == [
        f"c{level}" for level in range(1, 7)
    ]


def test_raster_metadata_is_propagated_without_pixels() -> None:
    schema = _schema()
    spec = schema.frame_for("seoul_landcover")
    dummy = pa.RecordBatch.from_pydict({"dummy": [0]})

    mapped = map_record_batch(
        dummy,
        spec,
        _inventory(spec.source_name),
        schema_version=schema.schema_version,
    )
    row = mapped.to_pylist()[0]

    assert row["width"] == 4
    assert row["height"] == 3
    assert row["resolution_x"] == 5.0
    assert row["nodata"] == "0"
    assert all("pixel" not in name for name in mapped.schema.names)
