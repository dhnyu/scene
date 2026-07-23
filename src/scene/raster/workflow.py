"""End-to-end M1.4.4 Raster Adapter workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.raster.metadata import RasterMetadataCollection
from scene.raster.reader import RasterReader
from scene.raster.reporting import write_raster_report
from scene.raster.serialize import RasterSerializer
from scene.raster.validator import RasterValidator


def _raster_paths(config: ProjectConfig) -> tuple[Path, ...]:
    selected = {
        source.source_name: source.path
        for source in config.sources
        if source.source_name in {"seoul_landcover", "seoul_dem"}
    }
    return tuple(selected[name] for name in ("seoul_landcover", "seoul_dem"))


def _stats(paths: tuple[Path, ...]) -> dict[str, tuple[int, int] | None]:
    snapshots: dict[str, tuple[int, int] | None] = {}
    for path in paths:
        try:
            stat = path.stat()
            snapshots[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            snapshots[str(path)] = None
    return snapshots


def run_raster(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate and serialize read-only raster references."""

    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_4_4_raster_adapter.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.4.4 Raster Adapter started")
    source_paths = _raster_paths(config)
    source_stats_before = _stats(source_paths)
    landcover, dem = RasterReader().read(config)
    validation = RasterValidator().validate(landcover, dem)
    collection = RasterMetadataCollection(
        landcover=landcover,
        dem=dem,
        grid_alignment=validation.grid_alignment,
    )
    logger.info(
        "Raster metadata validation completed",
        extra={
            "same_grid": validation.grid_alignment.same_grid,
            "valid": validation.valid,
        },
    )

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    artifacts = RasterSerializer().serialize(
        collection,
        validation,
        config.paths.output_root / "raster" / metadata.run_id,
        run_id=metadata.run_id,
    )
    source_stats_after = _stats(source_paths)
    input_stat_changes = tuple(
        path
        for path, value in source_stats_before.items()
        if value != source_stats_after[path]
    )
    reports = write_raster_report(
        collection,
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
    logger.info("M1.4.4 Raster Adapter completed", extra={"status": status})
    return {
        "failure_count": len(validation.issues) + len(input_stat_changes),
        "grid_alignment": validation.grid_alignment.to_dict(),
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "metadata_json": str(artifacts.metadata_json),
        "metadata_parquet": str(artifacts.metadata_parquet),
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "source_count": collection.source_count,
        "status": status,
        "validation": "PASS" if validation.valid else "FAIL",
    }
