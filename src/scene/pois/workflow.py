"""End-to-end M1.4.3 POI Adapter workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.pois.adapter import POIAdapter
from scene.pois.reader import POIReader, find_latest_canonical_manifest
from scene.pois.reporting import write_poi_report
from scene.pois.serialization import POISerializer
from scene.pois.validator import POIValidator
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


def run_pois(
    config_path: str | Path,
    *,
    canonical_manifest: str | Path | None = None,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Create and serialize POIDataset from M1.3 outputs only."""

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
        config.paths.logs_dir / f"{metadata.run_id}_m1_4_3_poi_adapter.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.4.3 POI Adapter started")
    source_paths = _source_paths(config)
    source_stats_before = _stats(source_paths)

    canonical_input = POIReader(
        schema, config.paths.output_root
    ).read(manifest_path)
    canonical_paths = (
        canonical_input.manifest_path,
        Path(canonical_input.geometry_provenance.canonical_frame_path),
        Path(canonical_input.attribute_provenance.canonical_frame_path),
    )
    canonical_stats_before = _stats(canonical_paths)
    result = POIAdapter(POIValidator(schema)).adapt(canonical_input)
    logger.info(
        "POIDataset validation completed",
        extra={
            "attribute_row_count": result.dataset.attribute_row_count,
            "feature_count": result.dataset.feature_count,
            "valid": result.validation.valid,
        },
    )

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    artifacts = POISerializer().serialize(
        result.dataset,
        result.validation,
        config.paths.output_root / "pois" / metadata.run_id,
        run_id=metadata.run_id,
    )

    before = {**source_stats_before, **canonical_stats_before}
    after = {**_stats(source_paths), **_stats(canonical_paths)}
    input_stat_changes = tuple(
        path for path, value in before.items() if value != after[path]
    )
    reports = write_poi_report(
        result.dataset,
        result.validation,
        artifacts,
        config.paths.reports_dir,
        metadata,
        input_stat_changes=input_stat_changes,
        verification=verification,
    )
    status = (
        "complete"
        if result.validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    logger.info("M1.4.3 POI Adapter completed", extra={"status": status})
    return {
        "attribute_parquet": str(artifacts.attribute_parquet),
        "failure_count": len(result.validation.issues)
        + len(input_stat_changes),
        "geometry_geopackage": str(artifacts.geometry_geopackage),
        "join_key_validation": (
            "PASS" if result.validation.join_key.valid else "FAIL"
        ),
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "metadata_json": str(artifacts.metadata_json),
        "poi_attribute_row_count": result.dataset.attribute_row_count,
        "poi_geometry_feature_count": result.dataset.feature_count,
        "category_path_validation": (
            "PASS" if result.validation.category_path_valid else "FAIL"
        ),
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "status": status,
        "validation": "PASS" if result.validation.valid else "FAIL",
    }
