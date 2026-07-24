from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import LineString, MultiPolygon, Point, Polygon, to_wkb
import yaml

from scene.inventory.hashing import sha256_file
from scene.schema.mapper import map_record_batch
from scene.schema.schema import load_canonical_schema


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def project_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "project.yaml"


@pytest.fixture
def canonical_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "contracts"
        / "canonical_schema.yaml"
    )


def make_config_data(root: Path) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "project_name": "scene-test",
        "timezone": "Asia/Seoul",
        "paths": {
            "project_root": str(root),
            "canonical_schema": str(root / "canonical_schema.yaml"),
            "input_root": str(root / "inputs"),
            "external_root": str(root / "external"),
            "output_root": str(root / "outputs"),
            "reports_dir": str(root / "reports"),
            "logs_dir": str(root / "logs"),
            "metadata_dir": str(root / "metadata"),
            "resolved_config_dir": str(root / "resolved"),
            "tmp_dir": str(root / "tmp"),
        },
        "storage": {
            "geometry_format": "geopackage",
            "tabular_format": "parquet",
            "parquet_compression": "zstd",
            "resolved_config_format": "yaml",
            "run_summary_format": "json",
            "miniature_raster_format": "geotiff",
            "source_raster_policy": "read_only_reference",
            "geopackage_usage": "inspection_and_archive",
            "per_scene_pt_files": "forbidden",
            "training_cache_format": "open",
        },
        "sources": [],
    }


def write_config(path: Path, data: Mapping[str, object]) -> Path:
    path.write_text(
        yaml.safe_dump(dict(data), sort_keys=False),
        encoding="utf-8",
    )
    return path


def make_building_canonical_fixture(
    root: Path,
    canonical_schema_path: Path,
) -> tuple[Path, object]:
    """Create a complete two-frame M1.3 building fixture."""

    schema = load_canonical_schema(canonical_schema_path)
    run_id = "20260724_010000_KST"
    output = root / "outputs" / "canonical" / run_id
    output.mkdir(parents=True)
    geometry_spec = schema.frame_for("seoul_buildings_geometry")
    attribute_spec = schema.frame_for("seoul_buildings_attributes")
    geometry_inventory = {
        "source_name": "seoul_buildings_geometry",
        "source_path": "/read-only/buildings.gpkg",
        "sha256": "a" * 64,
    }
    attribute_inventory = {
        "source_name": "seoul_buildings_attributes",
        "source_path": "/read-only/buildings.parquet",
        "sha256": "b" * 64,
    }
    geometry_source = pa.RecordBatch.from_pydict(
        {
            "fid": pa.array([1], type=pa.int64()),
            "building_id": ["native-building-1"],
            "geom": [
                to_wkb(
                    MultiPolygon(
                        [
                            Polygon(
                                [
                                    (200000.0, 550000.0),
                                    (200010.0, 550000.0),
                                    (200010.0, 550010.0),
                                    (200000.0, 550010.0),
                                    (200000.0, 550000.0),
                                ]
                            )
                        ]
                    )
                )
            ],
        }
    )
    geometry_batch = map_record_batch(
        geometry_source,
        geometry_spec,
        geometry_inventory,
        schema_version=schema.schema_version,
    )
    attribute_source = pa.RecordBatch.from_pydict(
        {
            "building_id": ["native-building-1"],
            "A9": ["use"],
            "A11": ["structure"],
            "A12": [100.0],
            "A16": [12.0],
            "source_feature_index": pa.array([9], type=pa.int32()),
            "source_zip": ["source.zip"],
            "source_layer": ["building"],
            "processed_at": ["2026-07-23"],
            "target_epsg": pa.array([5186], type=pa.int32()),
            "geometry_layer": ["seoul_buildings_vworld"],
        }
    )
    attribute_batch = map_record_batch(
        attribute_source,
        attribute_spec,
        attribute_inventory,
        schema_version=schema.schema_version,
        row_group_id=0,
        row_offset=0,
    )
    geometry_path = output / "seoul_buildings_geometry.parquet"
    attribute_path = output / "seoul_buildings_attributes.parquet"
    pq.write_table(
        pa.Table.from_batches([geometry_batch]),
        geometry_path,
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_batches([attribute_batch]),
        attribute_path,
        compression="zstd",
    )
    manifest = {
        "canonical_manifest_version": "1.0",
        "failure_count": 0,
        "frames": [
            {
                "frame_name": "building_geometry",
                "output_parquet": str(geometry_path),
                "output_sha256": sha256_file(geometry_path),
                "row_count": 1,
                "source_name": "seoul_buildings_geometry",
                "valid": True,
            },
            {
                "frame_name": "building_attribute",
                "output_parquet": str(attribute_path),
                "output_sha256": sha256_file(attribute_path),
                "row_count": 1,
                "source_name": "seoul_buildings_attributes",
                "valid": True,
            },
            {
                "frame_name": "road_link",
                "output_parquet": str(output / "must_not_be_read.parquet"),
                "output_sha256": "c" * 64,
                "row_count": 1,
                "source_name": "seoul_roads_links",
                "valid": True,
            },
        ],
        "run_id": run_id,
        "schema_name": schema.schema_name,
        "schema_path": str(canonical_schema_path),
        "schema_sha256": schema.sha256,
        "schema_validation_passed": True,
        "schema_version": schema.schema_version,
        "source_count": 3,
    }
    manifest_path = output / f"{run_id}_canonical_manifest.json"
    import json

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path, schema


def make_road_canonical_fixture(
    root: Path,
    canonical_schema_path: Path,
) -> tuple[Path, object]:
    """Create a complete two-frame M1.3 road fixture."""

    import json

    schema = load_canonical_schema(canonical_schema_path)
    run_id = "20260724_020000_KST"
    output = root / "outputs" / "canonical" / run_id
    output.mkdir(parents=True)
    link_spec = schema.frame_for("seoul_roads_links")
    node_spec = schema.frame_for("seoul_roads_nodes")
    link_inventory = {
        "source_name": "seoul_roads_links",
        "source_path": "/read-only/roads.gpkg",
        "sha256": "c" * 64,
    }
    node_inventory = {
        "source_name": "seoul_roads_nodes",
        "source_path": "/read-only/nodes.gpkg",
        "sha256": "d" * 64,
    }
    link_source = pa.RecordBatch.from_pydict(
        {
            "LINK_ID": ["link-1"],
            "F_NODE": ["node-1"],
            "T_NODE": ["node-2"],
            "LANES": pa.array([2], type=pa.int16()),
            "ROAD_RANK": ["rank"],
            "ROAD_TYPE": ["type"],
            "ROAD_NO": ["101"],
            "ROAD_NAME": ["road"],
            "LENGTH": [10.0],
            "fid": pa.array([1], type=pa.int64()),
            "geom": [
                to_wkb(
                    LineString(
                        [(200000.0, 550000.0), (200010.0, 550000.0)]
                    )
                )
            ],
        }
    )
    node_source = pa.RecordBatch.from_pydict(
        {
            "NODE_ID": ["node-1"],
            "NODE_TYPE": ["type"],
            "NODE_NAME": ["node"],
            "TURN_P": ["0"],
            "fid": pa.array([2], type=pa.int64()),
            "geom": [to_wkb(Point(200000.0, 550000.0))],
        }
    )
    link_batch = map_record_batch(
        link_source,
        link_spec,
        link_inventory,
        schema_version=schema.schema_version,
    )
    node_batch = map_record_batch(
        node_source,
        node_spec,
        node_inventory,
        schema_version=schema.schema_version,
    )
    link_path = output / "seoul_roads_links.parquet"
    node_path = output / "seoul_roads_nodes.parquet"
    pq.write_table(
        pa.Table.from_batches([link_batch]),
        link_path,
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_batches([node_batch]),
        node_path,
        compression="zstd",
    )
    frames = [
        {
            "frame_name": "road_link",
            "output_parquet": str(link_path),
            "output_sha256": sha256_file(link_path),
            "row_count": 1,
            "source_name": "seoul_roads_links",
            "valid": True,
        },
        {
            "frame_name": "road_node",
            "output_parquet": str(node_path),
            "output_sha256": sha256_file(node_path),
            "row_count": 1,
            "source_name": "seoul_roads_nodes",
            "valid": True,
        },
        {
            "frame_name": "building_geometry",
            "output_parquet": str(output / "must_not_be_read.parquet"),
            "output_sha256": "e" * 64,
            "row_count": 1,
            "source_name": "seoul_buildings_geometry",
            "valid": True,
        },
    ]
    manifest = {
        "canonical_manifest_version": "1.0",
        "failure_count": 0,
        "frames": frames,
        "run_id": run_id,
        "schema_name": schema.schema_name,
        "schema_path": str(canonical_schema_path),
        "schema_sha256": schema.sha256,
        "schema_validation_passed": True,
        "schema_version": schema.schema_version,
        "source_count": len(frames),
    }
    manifest_path = output / f"{run_id}_canonical_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path, schema


def make_poi_canonical_fixture(
    root: Path,
    canonical_schema_path: Path,
) -> tuple[Path, object]:
    """Create complete two-frame M1.3 POI fixtures."""

    import json

    schema = load_canonical_schema(canonical_schema_path)
    run_id = "20260724_030000_KST"
    output = root / "outputs" / "canonical" / run_id
    output.mkdir(parents=True)
    geometry_spec = schema.frame_for("seoul_poi_geometry")
    attribute_spec = schema.frame_for("seoul_poi_attributes")
    geometry_inventory = {
        "source_name": "seoul_poi_geometry",
        "source_path": "/read-only/pois.gpkg",
        "sha256": "f" * 64,
    }
    attribute_inventory = {
        "source_name": "seoul_poi_attributes",
        "source_path": "/read-only/pois.parquet",
        "sha256": "1" * 64,
    }
    geometry_source = pa.RecordBatch.from_pydict(
        {
            "NF_ID": ["poi-1", "poi-2"],
            "fid": pa.array([1, 2], type=pa.int64()),
            "geom": [
                to_wkb(Point(200000.0, 550000.0)),
                to_wkb(Point(200010.0, 550010.0)),
            ],
        }
    )
    attribute_source = pa.RecordBatch.from_pydict(
        {
            "NF_ID": ["poi-1", "poi-2"],
            "POI_CL_DC_1": ["A1", "A1"],
            "POI_CL_DC_2": ["B1", "B2"],
            "POI_CL_DC_3": ["C1", "C2"],
            "POI_CL_DC_4": ["D1", "D2"],
            "POI_CL_DC_5": ["E1", "E2"],
            "POI_CL_DC_6": ["F1", "F2"],
        }
    )
    geometry_batch = map_record_batch(
        geometry_source,
        geometry_spec,
        geometry_inventory,
        schema_version=schema.schema_version,
    )
    attribute_batch = map_record_batch(
        attribute_source,
        attribute_spec,
        attribute_inventory,
        schema_version=schema.schema_version,
        row_group_id=0,
        row_offset=0,
    )
    geometry_path = output / "seoul_poi_geometry.parquet"
    attribute_path = output / "seoul_poi_attributes.parquet"
    pq.write_table(
        pa.Table.from_batches([geometry_batch]),
        geometry_path,
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_batches([attribute_batch]),
        attribute_path,
        compression="zstd",
    )
    frames = [
        {
            "frame_name": "poi_geometry",
            "output_parquet": str(geometry_path),
            "output_sha256": sha256_file(geometry_path),
            "row_count": 2,
            "source_name": "seoul_poi_geometry",
            "valid": True,
        },
        {
            "frame_name": "poi_attribute",
            "output_parquet": str(attribute_path),
            "output_sha256": sha256_file(attribute_path),
            "row_count": 2,
            "source_name": "seoul_poi_attributes",
            "valid": True,
        },
        {
            "frame_name": "dem_metadata",
            "output_parquet": str(output / "must_not_be_read.parquet"),
            "output_sha256": "2" * 64,
            "row_count": 1,
            "source_name": "seoul_dem",
            "valid": True,
        },
    ]
    manifest = {
        "canonical_manifest_version": "1.0",
        "failure_count": 0,
        "frames": frames,
        "run_id": run_id,
        "schema_name": schema.schema_name,
        "schema_path": str(canonical_schema_path),
        "schema_sha256": schema.sha256,
        "schema_validation_passed": True,
        "schema_version": schema.schema_version,
        "source_count": len(frames),
    }
    manifest_path = output / f"{run_id}_canonical_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path, schema


def make_raster_config_fixture(root: Path) -> Path:
    """Create two small GeoTIFF sources and their project configuration."""

    import subprocess

    inputs = root / "inputs"
    inputs.mkdir()
    (root / "external").mkdir()
    landcover = inputs / "landcover.tif"
    dem = inputs / "dem.tif"
    commands = (
        [
            "gdal_create",
            "-of",
            "GTiff",
            "-outsize",
            "4",
            "3",
            "-bands",
            "1",
            "-ot",
            "Byte",
            "-a_srs",
            "EPSG:5186",
            "-a_ullr",
            "100",
            "200",
            "120",
            "185",
            "-a_nodata",
            "0",
            "-co",
            "COMPRESS=DEFLATE",
            str(landcover),
        ],
        [
            "gdal_create",
            "-of",
            "GTiff",
            "-outsize",
            "2",
            "2",
            "-bands",
            "1",
            "-ot",
            "Float32",
            "-a_srs",
            "EPSG:5186",
            "-a_ullr",
            "90",
            "210",
            "150",
            "150",
            "-a_nodata",
            "-32767",
            "-co",
            "COMPRESS=DEFLATE",
            str(dem),
        ],
    )
    for command in commands:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    config = make_config_data(root)
    config["sources"] = [
        {
            "source_name": "seoul_landcover",
            "category": "landcover",
            "kind": "raster",
            "path": "landcover.tif",
        },
        {
            "source_name": "seoul_dem",
            "category": "dem",
            "kind": "raster",
            "path": "dem.tif",
        },
    ]
    return write_config(root / "project.yaml", config)


def make_stable_id_canonical_fixture(
    root: Path,
    canonical_schema_path: Path,
) -> tuple[Path, Path]:
    """Create four minimal M1.3 geometry frames for M1.5 tests."""

    import json

    schema = load_canonical_schema(canonical_schema_path)
    (root / "inputs").mkdir()
    (root / "external").mkdir()
    run_id = "20260724_060000_KST"
    output = root / "outputs" / "canonical" / run_id
    output.mkdir(parents=True)
    definitions = (
        (
            "seoul_buildings_geometry",
            "building_geometry",
            "source_building_id",
            ["0001", "0002"],
            "a" * 64,
            "/read-only/buildings.gpkg",
        ),
        (
            "seoul_roads_links",
            "road_link",
            "source_link_id",
            ["0001"],
            "b" * 64,
            "/read-only/links.gpkg",
        ),
        (
            "seoul_roads_nodes",
            "road_node",
            "source_node_id",
            ["0001"],
            "c" * 64,
            "/read-only/nodes.gpkg",
        ),
        (
            "seoul_poi_geometry",
            "poi_geometry",
            "source_poi_id",
            ["0001", "0002"],
            "d" * 64,
            "/read-only/pois.gpkg",
        ),
    )
    frames: list[dict[str, object]] = []
    for source_name, frame_name, native_field, native_ids, source_hash, path in (
        definitions
    ):
        table_schema = pa.schema(
            [
                pa.field("source_name", pa.string(), nullable=False),
                pa.field("source_path", pa.string(), nullable=False),
                pa.field("source_file_sha256", pa.string(), nullable=False),
                pa.field(native_field, pa.string(), nullable=False),
                pa.field("source_fid", pa.int64(), nullable=False),
            ]
        )
        table = pa.Table.from_arrays(
            [
                pa.array([source_name] * len(native_ids), type=pa.string()),
                pa.array([path] * len(native_ids), type=pa.string()),
                pa.array([source_hash] * len(native_ids), type=pa.string()),
                pa.array(native_ids, type=pa.string()),
                pa.array(
                    range(1, len(native_ids) + 1),
                    type=pa.int64(),
                ),
            ],
            schema=table_schema,
        )
        frame_path = output / f"{source_name}.parquet"
        pq.write_table(table, frame_path, compression="zstd")
        frames.append(
            {
                "frame_name": frame_name,
                "output_parquet": str(frame_path),
                "output_sha256": sha256_file(frame_path),
                "row_count": len(native_ids),
                "source_name": source_name,
                "valid": True,
            }
        )
    manifest = {
        "canonical_manifest_version": "1.0",
        "failure_count": 0,
        "frames": frames,
        "run_id": run_id,
        "schema_name": schema.schema_name,
        "schema_path": str(canonical_schema_path),
        "schema_sha256": schema.sha256,
        "schema_validation_passed": True,
        "schema_version": schema.schema_version,
        "source_count": len(frames),
    }
    manifest_path = output / f"{run_id}_canonical_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    config = make_config_data(root)
    config["paths"]["canonical_schema"] = str(canonical_schema_path)
    config_path = write_config(root / "project.yaml", config)
    return config_path, manifest_path
