from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import MultiPolygon, Polygon, to_wkb
import yaml

from scene.inventory.hashing import sha256_file
from scene.schema.mapper import map_record_batch
from scene.schema.schema import load_canonical_schema


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
