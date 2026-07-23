"""End-to-end M1.4.1 Building Adapter workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from scene.buildings.adapter import BuildingAdapter
from scene.buildings.reader import BuildingReader, find_latest_canonical_manifest
from scene.buildings.reporting import write_building_report
from scene.buildings.serialization import BuildingSerializer
from scene.buildings.validator import BuildingValidator
from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.schema.schema import load_canonical_schema


def _stats(paths: tuple[Path, ...]) -> dict[str, tuple[int, int] | None]:
    snapshots: dict[str, tuple[int, int] | None] = {}
    for path in paths:
        try:
            stat = path.stat()
            snapshots[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            snapshots[str(path)] = None
    return snapshots


def _source_paths(config: ProjectConfig) -> tuple[Path, ...]:
    return tuple(source.path for source in config.sources)


def run_buildings(
    config_path: str | Path,
    *,
    canonical_manifest: str | Path | None = None,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Create and serialize the BuildingDataset from M1.3 outputs only."""

    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    schema = load_canonical_schema(config.paths.canonical_schema)
    manifest_path = (
        Path(canonical_manifest).expanduser().resolve(strict=False)
        if canonical_manifest is not None
        else find_latest_canonical_manifest(config.paths.output_root)
    )
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_4_1_building_adapter.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.4.1 Building Adapter started")
    source_paths = _source_paths(config)
    source_stats_before = _stats(source_paths)

    reader = BuildingReader(schema, config.paths.output_root)
    canonical_input = reader.read(manifest_path)
    canonical_paths = (
        canonical_input.manifest_path,
        Path(canonical_input.geometry_provenance.canonical_frame_path),
        Path(canonical_input.attribute_provenance.canonical_frame_path),
    )
    canonical_stats_before = _stats(canonical_paths)
    adapter_result = BuildingAdapter(BuildingValidator(schema)).adapt(
        canonical_input
    )
    dataset = adapter_result.dataset
    validation = adapter_result.validation
    logger.info(
        "BuildingDataset validation completed",
        extra={
            "feature_count": dataset.feature_count,
            "valid": validation.valid,
        },
    )

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    output_directory = (
        config.paths.output_root / "buildings" / metadata.run_id
    )
    artifacts = BuildingSerializer().serialize(
        dataset,
        validation,
        output_directory,
        run_id=metadata.run_id,
    )

    source_stats_after = _stats(source_paths)
    canonical_stats_after = _stats(canonical_paths)
    input_stat_changes = tuple(
        path
        for path, before in {
            **source_stats_before,
            **canonical_stats_before,
        }.items()
        if before
        != {
            **source_stats_after,
            **canonical_stats_after,
        }[path]
    )
    reports = write_building_report(
        dataset,
        validation,
        artifacts,
        config.paths.reports_dir,
        metadata,
        input_stat_changes=input_stat_changes,
        verification=verification,
    )
    status = (
        "complete"
        if validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    logger.info(
        "M1.4.1 Building Adapter completed",
        extra={
            "feature_count": dataset.feature_count,
            "status": status,
        },
    )
    return {
        "attribute_parquet": str(artifacts.attribute_parquet),
        "building_feature_count": dataset.feature_count,
        "building_metadata_json": str(artifacts.metadata_json),
        "failure_count": len(validation.issues) + len(input_stat_changes),
        "geometry_geopackage": str(artifacts.geometry_geopackage),
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "status": status,
        "validation": "PASS" if validation.valid else "FAIL",
    }
