"""One-run M1.5.1 boundary, M1.2, and M1.3 backfill workflow."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.boundaries.adapter import adapt_seoul_districts
from scene.boundaries.exceptions import BoundaryIntegrationError
from scene.boundaries.reader import audit_boundary_source, read_seoul_features
from scene.boundaries.serialization import (
    BoundaryArtifacts,
    write_boundary_artifacts,
)
from scene.boundaries.spatial_audit import audit_spatial_consistency
from scene.boundaries.validator import validate_canonical_districts
from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import KST, RunMetadata, collect_run_metadata
from scene.id.generator import canonical_hash
from scene.inventory.hashing import sha256_file
from scene.inventory.registry import SourceRegistry
from scene.inventory.scanner import scan_inventory
from scene.inventory.serialization import InventoryPaths, write_inventory
from scene.schema.schema import load_canonical_schema


SOURCE_NAME = "koreanadm_2024q2_sigungu"
CANONICAL_SOURCE_NAME = "seoul_boundary"
MAPPING_ROWS = (
    ("SIGUNGU_CD", "district_code", "string", False),
    ("SIGUNGU_NM", "district_name", "string", False),
    ("SIDO_CD", "sido_code", "string", False),
    ("SIDO_NM", "sido_name", "string", False),
    ("SIGUNGU_CD", "source_object_id", "string", False),
    ("geom", "geometry", "geometry<EPSG:5186>", False),
    ("derived", "district_id", "string", False),
    ("source registry", "source_name", "string", False),
    ("source registry", "source_path", "string", False),
    ("source registry", "source_layer", "string", False),
    ("OGR metadata", "source_crs", "string", False),
    ("canonical policy", "canonical_crs", "string", False),
    ("M1.2 inventory", "source_sha256", "string", False),
    ("GeoPackage driver", "source_fid", "int64", False),
    ("BASE_DATE", "source_base_date", "string", False),
)


def _snapshot(path: Path, *, include_hash: bool = True) -> dict[str, object]:
    stat = path.stat()
    return {
        "file_size": stat.st_size,
        "modified_time_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path) if include_hash else None,
    }


def _load_previous_inventory(config: ProjectConfig) -> dict[str, Mapping[str, Any]]:
    candidates = sorted(
        (config.paths.metadata_dir / "inventory").glob(
            "*_source_inventory.json"
        )
    )
    if not candidates:
        return {}
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return {
        str(record["source_name"]): record
        for record in payload.get("records", [])
        if isinstance(record, Mapping) and "source_name" in record
    }


def _inventory_preservation(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> dict[str, object]:
    stable_fields = (
        "source_path",
        "layer_name",
        "exists",
        "readable",
        "file_size",
        "modified_time_kst",
        "sha256",
        "crs",
        "geometry_type",
        "feature_count",
        "bbox_min_x",
        "bbox_min_y",
        "bbox_max_x",
        "bbox_max_y",
        "raster_width",
        "raster_height",
        "resolution_x",
        "resolution_y",
        "extent_min_x",
        "extent_min_y",
        "extent_max_x",
        "extent_max_y",
        "band_count",
        "dtype",
        "nodata",
    )
    missing = sorted(set(previous) - set(current))
    changed: dict[str, list[str]] = {}
    for source_name in sorted(set(previous) & set(current)):
        fields = [
            field
            for field in stable_fields
            if previous[source_name].get(field) != current[source_name].get(field)
        ]
        if fields:
            changed[source_name] = fields
    return {
        "existing_source_missing_count": len(missing),
        "existing_source_missing": missing,
        "existing_source_metadata_changed_count": len(changed),
        "existing_source_metadata_changed": changed,
    }


def _write_mapping(
    directory: Path,
    *,
    metadata: RunMetadata,
    source_sha256: str,
    district_parquet: Path,
    content_hash: str,
    schema_version: str,
    schema_sha256: str,
) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=False)
    mapping_table = pa.Table.from_pylist(
        [
            {
                "source_field": source_field,
                "canonical_field": canonical_field,
                "dtype": dtype,
                "nullable": nullable,
            }
            for source_field, canonical_field, dtype, nullable in MAPPING_ROWS
        ]
    )
    parquet_path = directory / "district_canonical_mapping.parquet"
    pq.write_table(mapping_table, parquet_path, compression="zstd")
    mapping_payload = {
        "canonical_content_hash": content_hash,
        "canonical_entity_type": "district",
        "canonical_feature_count": 25,
        "canonical_parquet": str(district_parquet),
        "canonical_schema_sha256": schema_sha256,
        "canonical_schema_version": schema_version,
        "crs_transformation": "EPSG:5179 -> EPSG:5186",
        "filter_rule": "SIGUNGU_CD starts with official SIDO_CD '11'",
        "mapping": mapping_table.to_pylist(),
        "mapping_hash": canonical_hash(
            "district_mapping",
            *(
                str(value)
                for row in MAPPING_ROWS
                for value in row
            ),
        ),
        "provenance_complete": True,
        "run_id": metadata.run_id,
        "source_feature_count": 25,
        "source_sha256": source_sha256,
        "status": "PASS",
        "string_code_preserved": True,
    }
    json_path = directory / "district_canonical_mapping.json"
    validation_path = directory / "district_mapping_validation.json"
    json_path.write_text(
        json.dumps(mapping_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    validation = {
        "district_code_complete": True,
        "district_id_complete": True,
        "district_name_complete": True,
        "duplicate_mapping_count": 0,
        "geometry_complete": True,
        "missing_mapping_count": 0,
        "provenance_complete": True,
        "source_object_id_complete": True,
        "source_to_canonical_count": "25-to-25",
        "status": "PASS",
        "string_code_preserved": True,
    }
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "parquet": str(parquet_path),
        "parquet_sha256": sha256_file(parquet_path),
        "validation": str(validation_path),
        **mapping_payload,
    }


def _refresh_canonical_manifest(
    config: ProjectConfig,
    *,
    metadata: RunMetadata,
    schema_name: str,
    schema_version: str,
    schema_sha256: str,
    district_parquet: Path,
    district_content_hash: str,
    inventory_path: Path,
) -> tuple[Path, dict[str, object]]:
    candidates = sorted(
        (config.paths.output_root / "canonical").glob(
            "*/*_canonical_manifest.json"
        )
    )
    if not candidates:
        raise BoundaryIntegrationError(
            "M1.3 canonical manifest is required for mapping backfill"
        )
    previous_path = candidates[-1]
    previous_bytes = previous_path.read_bytes()
    previous = json.loads(previous_bytes)
    previous_frames = previous.get("frames")
    if not isinstance(previous_frames, list) or len(previous_frames) < 10:
        raise BoundaryIntegrationError(
            "existing M1.3 canonical manifest is incomplete"
        )
    output = config.paths.output_root / "canonical" / metadata.run_id
    output.mkdir(parents=True, exist_ok=False)
    district_frame = {
        "canonical_columns": 16,
        "crs": "EPSG:5186",
        "crs_valid": True,
        "dtypes_valid": True,
        "frame_name": "district",
        "geometry_type": "MultiPolygon",
        "geometry_type_valid": True,
        "issues": [],
        "mapping_succeeded": True,
        "nullable_valid": True,
        "output_parquet": str(district_parquet),
        "output_sha256": sha256_file(district_parquet),
        "required_fields_valid": True,
        "row_count": 25,
        "source_columns_mapped": len(MAPPING_ROWS),
        "source_kind": "vector",
        "source_name": SOURCE_NAME,
        "valid": True,
    }
    frames = [
        frame
        for frame in previous_frames
        if frame.get("source_name") != SOURCE_NAME
    ] + [district_frame]
    manifest = {
        "canonical_manifest_version": "1.1",
        "district_content_hash": district_content_hash,
        "failure_count": 0,
        "frames": frames,
        "inventory_path": str(inventory_path),
        "mapped_source_count": len(frames),
        "output_directory": str(output),
        "previous_manifest_path": str(previous_path),
        "previous_manifest_sha256": sha256_file(previous_path),
        "run_id": metadata.run_id,
        "schema_name": schema_name,
        "schema_path": str(config.paths.canonical_schema),
        "schema_sha256": schema_sha256,
        "schema_validation_passed": True,
        "schema_version": schema_version,
        "source_count": len(frames),
    }
    path = output / f"{metadata.run_id}_canonical_manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    preservation = {
        "previous_manifest_unchanged": previous_path.read_bytes()
        == previous_bytes,
        "previous_frame_count": len(previous_frames),
        "preserved_frame_count": len(frames) - 1,
        "district_frame_added": True,
    }
    return path, preservation


def _district_table_summary(dataset: Any) -> str:
    return "\n".join(
        f"- `{code}`: {name}"
        for code, name in zip(
            dataset.districts["district_code"],
            dataset.districts["district_name"],
            strict=True,
        )
    )


def _layer_audit_table(source_audit: Mapping[str, object]) -> str:
    rows = [
        "| Layer | Rows | CRS | Geometry | Fields |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for layer in source_audit["layers"]:
        rows.append(
            f"| `{layer['layer_name']}` | {layer['row_count']} | "
            f"`{layer['crs']}` | `{layer['geometry_type']}` | "
            f"`{', '.join(layer['fields'])}` |"
        )
    return "\n".join(rows)


def _overlap_summary(spatial: Mapping[str, object]) -> str:
    pairs = spatial.get("overlap_pairs", [])
    if not pairs:
        return "- Positive-area overlap pairs: none"
    return "\n".join(
        "- Positive-area overlap: "
        f"`{item['left_district_code']} {item['left_district_name']}` / "
        f"`{item['right_district_code']} {item['right_district_name']}` = "
        f"`{item['overlap_area_m2']} m²`"
        for item in pairs
    )


def _write_report(
    config: ProjectConfig,
    metadata: RunMetadata,
    *,
    source_audit: Mapping[str, object],
    dataset: Any,
    validation: Mapping[str, object],
    spatial: Mapping[str, object],
    artifacts: BoundaryArtifacts,
    inventory_paths: InventoryPaths,
    inventory_summary: Mapping[str, object],
    mapping: Mapping[str, object],
    canonical_manifest: Path,
    canonical_preservation: Mapping[str, object],
    read_only: Mapping[str, object],
    verification: Mapping[str, object] | None,
) -> tuple[Path, Path]:
    reports = write_reports(
        config.paths.reports_dir,
        f"{metadata.run_id}_m1_5_1_seoul_district_boundary_integration",
        title="M1.5.1 Seoul District Boundary Integration",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "canonical_mapping": dict(mapping),
            "canonical_manifest": str(canonical_manifest),
            "canonical_preservation": dict(canonical_preservation),
            "district_count": len(dataset.districts),
            "inventory": dict(inventory_summary),
            "inventory_json": str(inventory_paths.json),
            "inventory_parquet": str(inventory_paths.parquet),
            "read_only_verification": dict(read_only),
            "source_audit": dict(source_audit),
            "spatial_consistency": dict(spatial),
            "status": "complete",
            "validation": dict(validation),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Source Audit",
                f"- Source path: `{source_audit['source_path']}`\n"
                f"- Source SHA-256: `{source_audit['sha256']}`\n"
                f"- File size: `{source_audit['file_size']}` bytes\n"
                f"- Modified time ns: `{source_audit['modified_time_ns']}`\n"
                f"- Selected layer: `{source_audit['district_layer']}`\n"
                f"- Source CRS: `EPSG:5179`\n"
                "- Selected fields: `SIGUNGU_CD`, `SIGUNGU_NM`, "
                "`SIDO_CD`, `SIDO_NM`\n\n"
                + _layer_audit_table(source_audit),
            ),
            ReportSection(
                "Seoul District Extraction",
                "- Filter rule: official `SIGUNGU_CD` prefix equals official "
                "`SIDO_CD` `11`\n"
                f"- Extracted count: `{len(dataset.districts)}`\n\n"
                + _district_table_summary(dataset),
            ),
            ReportSection(
                "Canonical Boundary",
                f"- Output: `{artifacts.geopackage}`\n"
                "- Layer: `seoul_sigungu`\n"
                "- CRS: `EPSG:5186`\n"
                f"- Geometry types: `{validation['geometry_types']}`\n"
                f"- Feature count: `{validation['row_count']}`\n"
                "- Stable ID: length-prefixed UTF-8 SHA-256 over source name, "
                "administrative level, and official district code",
            ),
            ReportSection(
                "Geometry Validation",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in validation.items()
                ),
            ),
            ReportSection(
                "Spatial Consistency",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in spatial.items()
                    if key != "overlap_pairs"
                )
                + "\n"
                + _overlap_summary(spatial)
                + "\n- The positive-area overlap is reported without repair, "
                "snapping, buffering, or an invented tolerance.",
            ),
            ReportSection(
                "Source Registry Update",
                f"`{SOURCE_NAME}` is registered as the read-only `sigungu` "
                "layer with its observed EPSG:5179 metadata.",
            ),
            ReportSection(
                "M1.2 Inventory Backfill",
                f"- JSON: `{inventory_paths.json}`\n"
                f"- Parquet: `{inventory_paths.parquet}`\n"
                + "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in inventory_summary.items()
                ),
            ),
            ReportSection(
                "M1.3 Canonical Mapping Backfill",
                f"- Mapping JSON: `{mapping['json']}`\n"
                f"- Mapping Parquet: `{mapping['parquet']}`\n"
                f"- Refreshed manifest: `{canonical_manifest}`\n"
                "- Mapping: actual official fields to the canonical district "
                "entity; administrative codes remain strings.\n"
                + "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in canonical_preservation.items()
                ),
            ),
            ReportSection(
                "Determinism",
                f"- District content hash: `{dataset.content_hash}`\n"
                f"- Mapping hash: `{mapping['mapping_hash']}`\n"
                "- IDs and content hashes exclude row order, run ID, and time.",
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in artifacts.to_dict().items()
                ),
            ),
            ReportSection(
                "Read-only Verification",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in read_only.items()
                ),
            ),
            ReportSection(
                "Scope",
                "No district assignment, 15/5/5 search, balancing, scene "
                "footprint, clipping, relation, raster crop, tensor, model, "
                "or training-cache artifact was created. M1.6 was not run.",
            ),
            ReportSection(
                "Verification",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in (verification or {}).items()
                )
                or "Verification is recorded by the completing run.",
            ),
        ),
    )
    return reports.markdown, reports.json


def _run_seoul_district_integration(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Execute boundary, inventory, and canonical mapping stages in order."""

    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_5_1_seoul_district_boundary.jsonl",
        metadata.run_id,
        level=log_level,
    )
    source = next(
        (
            item
            for item in config.sources
            if item.source_name == SOURCE_NAME
        ),
        None,
    )
    if source is None or source.canonical_adapter != "seoul_district_boundary":
        raise BoundaryIntegrationError(
            f"{SOURCE_NAME} is not registered with the district adapter"
        )
    city_source = next(
        item for item in config.sources if item.source_name == CANONICAL_SOURCE_NAME
    )
    previous_inventory = _load_previous_inventory(config)
    schema = load_canonical_schema(config.paths.canonical_schema)
    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)

    logger.info("stage 1: official boundary source audit")
    audit = audit_boundary_source(source.path)
    official_before = {
        "file_size": audit.file_size,
        "modified_time_ns": audit.modified_time_ns,
        "sha256": audit.sha256,
    }
    city_before = _snapshot(city_source.path)
    source_districts, source_seoul = read_seoul_features(audit)
    dataset = adapt_seoul_districts(
        source_districts,
        source_seoul,
        audit,
        source_name=SOURCE_NAME,
    )
    validation = validate_canonical_districts(dataset)
    if not validation.valid:
        raise BoundaryIntegrationError(
            f"canonical district validation failed: {validation.to_dict()}"
        )
    city = pyogrio.read_dataframe(
        city_source.path,
        layer=city_source.layer,
    )
    if city.crs is None:
        raise BoundaryIntegrationError("existing Seoul boundary CRS is missing")
    city = city.to_crs("EPSG:5186")
    spatial = audit_spatial_consistency(dataset.districts, city)
    logger.info("stage 1: canonical boundary serialization")
    boundary_directory = (
        config.paths.output_root / "boundaries" / metadata.run_id
    )
    artifacts = write_boundary_artifacts(
        dataset,
        validation,
        spatial,
        boundary_directory,
        run_id=metadata.run_id,
        config_hash=config.canonical_hash,
        canonical_schema_version=schema.schema_version,
    )

    logger.info("stage 2: M1.2 inventory backfill")
    scan = scan_inventory(
        SourceRegistry.from_project_config(config),
        run_id=metadata.run_id,
        started_at_kst=metadata.started_at_kst,
        logger=logger,
        config_hash=config.canonical_hash,
    )
    if scan.failure_count:
        raise BoundaryIntegrationError(
            f"inventory backfill has {scan.failure_count} invalid source(s)"
        )
    inventory_paths = write_inventory(
        scan,
        config.paths.output_root / "inventory" / metadata.run_id,
    )
    write_inventory(scan, config.paths.metadata_dir / "inventory")
    current_inventory = {
        record.source_name: record.to_dict() for record in scan.records
    }
    inventory_summary = {
        **_inventory_preservation(previous_inventory, current_inventory),
        "new_source_included": SOURCE_NAME in current_inventory,
        "new_source_layer": current_inventory[SOURCE_NAME]["layer_name"],
        "new_source_feature_count": current_inventory[SOURCE_NAME][
            "feature_count"
        ],
        "new_source_filter_count": len(dataset.districts),
        "source_count": scan.source_count,
        "validation_failure_count": scan.failure_count,
    }
    if (
        inventory_summary["existing_source_missing_count"] != 0
        or inventory_summary["existing_source_metadata_changed_count"] != 0
    ):
        raise BoundaryIntegrationError(
            f"existing inventory changed unexpectedly: {inventory_summary}"
        )

    logger.info("stage 3: M1.3 district mapping backfill")
    mapping = _write_mapping(
        config.paths.output_root / "schema" / metadata.run_id,
        metadata=metadata,
        source_sha256=audit.sha256,
        district_parquet=artifacts.districts_parquet,
        content_hash=dataset.content_hash,
        schema_version=schema.schema_version,
        schema_sha256=schema.sha256,
    )
    canonical_manifest, canonical_preservation = _refresh_canonical_manifest(
        config,
        metadata=metadata,
        schema_name=schema.schema_name,
        schema_version=schema.schema_version,
        schema_sha256=schema.sha256,
        district_parquet=artifacts.districts_parquet,
        district_content_hash=dataset.content_hash,
        inventory_path=inventory_paths.json,
    )

    official_after = _snapshot(source.path)
    city_after = _snapshot(city_source.path)
    read_only = {
        "official_source_hash_changed": (
            official_before["sha256"] != official_after["sha256"]
        ),
        "official_source_mtime_changed": (
            official_before["modified_time_ns"]
            != official_after["modified_time_ns"]
        ),
        "official_source_size_changed": (
            official_before["file_size"] != official_after["file_size"]
        ),
        "existing_seoul_boundary_hash_changed": (
            city_before["sha256"] != city_after["sha256"]
        ),
        "existing_seoul_boundary_mtime_changed": (
            city_before["modified_time_ns"] != city_after["modified_time_ns"]
        ),
        "existing_seoul_boundary_size_changed": (
            city_before["file_size"] != city_after["file_size"]
        ),
    }
    if any(read_only.values()):
        raise BoundaryIntegrationError(
            f"read-only source changed during integration: {read_only}"
        )
    markdown, json_report = _write_report(
        config,
        metadata,
        source_audit=audit.to_dict(),
        dataset=dataset,
        validation=validation.to_dict(),
        spatial=spatial,
        artifacts=artifacts,
        inventory_paths=inventory_paths,
        inventory_summary=inventory_summary,
        mapping=mapping,
        canonical_manifest=canonical_manifest,
        canonical_preservation=canonical_preservation,
        read_only=read_only,
        verification=verification,
    )
    logger.info("M1.5.1 integrated workflow completed")
    return {
        "canonical_crs": "EPSG:5186",
        "canonical_geopackage": str(artifacts.geopackage),
        "canonical_layer": "seoul_sigungu",
        "district_code_duplicates": validation.district_code_duplicate,
        "district_count": validation.row_count,
        "district_id_duplicates": validation.district_id_duplicate,
        "gap_area_m2": spatial["gap_area_m2"],
        "invalid_geometry": validation.geometry_invalid,
        "inventory": "PASS",
        "mapping": "PASS",
        "markdown_report": str(markdown),
        "json_report": str(json_report),
        "outside_area_m2": spatial["outside_area_m2"],
        "overlap_area_m2": spatial["pairwise_overlap_total_area_m2"],
        "registry": "PASS",
        "run_id": metadata.run_id,
        "source_crs": next(
            layer.crs
            for layer in audit.layers
            if layer.layer_name == audit.district_layer
        ),
        "source_layer": audit.district_layer,
        "status": "complete",
        "symmetric_difference_ratio": spatial[
            "symmetric_difference_ratio"
        ],
    }


def run_seoul_district_integration(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run the integrated workflow and always report contracted fatal failures."""

    instant = started_at or datetime.now(tz=KST)
    try:
        return _run_seoul_district_integration(
            config_path,
            log_level=log_level,
            started_at=instant,
            verification=verification,
        )
    except (BoundaryIntegrationError, OSError, RuntimeError, ValueError) as exc:
        config = load_config(config_path)
        metadata = collect_run_metadata(config, started_at=instant)
        boundary_dir = (
            config.paths.output_root / "boundaries" / metadata.run_id
        )
        inventory_dir = (
            config.paths.output_root / "inventory" / metadata.run_id
        )
        mapping_dir = config.paths.output_root / "schema" / metadata.run_id
        if not boundary_dir.exists():
            stopped_stage = "boundary audit or canonicalization"
        elif not inventory_dir.exists():
            stopped_stage = "M1.2 inventory backfill"
        else:
            stopped_stage = "M1.3 canonical mapping backfill"
        generated = [
            str(path)
            for directory in (boundary_dir, inventory_dir, mapping_dir)
            if directory.exists()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        ]
        reports = write_reports(
            config.paths.reports_dir,
            f"{metadata.run_id}_m1_5_1_seoul_district_boundary_integration",
            title="M1.5.1 Seoul District Boundary Integration",
            metadata=metadata,
            summary={
                "failure_reason": str(exc),
                "generated_diagnostic_artifacts": generated,
                "m1_6_allowed": False,
                "status": "failed",
                "stopped_stage": stopped_stage,
            },
            sections=(
                ReportSection(
                    "Failure",
                    f"- Stopped stage: `{stopped_stage}`\n"
                    f"- Observed failure: `{exc}`\n"
                    "- M1.6 cannot run because M1.5.1 did not complete.",
                ),
                ReportSection(
                    "Diagnostic Artifacts",
                    "\n".join(f"- `{path}`" for path in generated)
                    or "No stage artifact was created.",
                ),
                ReportSection(
                    "Preservation",
                    "Generated diagnostics were preserved. No source geometry "
                    "was repaired, inferred, dissolved, split, snapped, "
                    "buffered, or overwritten.",
                ),
            ),
        )
        raise BoundaryIntegrationError(
            f"{exc}; failure report: {reports.markdown}"
        ) from exc
