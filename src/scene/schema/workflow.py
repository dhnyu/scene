"""End-to-end M1.3 canonical validation and mapping workflow."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import Mapping

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.schema.inventory import find_latest_inventory, load_inventory_records
from scene.schema.models import CanonicalRunResult
from scene.schema.reporting import write_canonical_report
from scene.schema.schema import load_canonical_schema
from scene.schema.serialization import write_canonical_manifest
from scene.schema.sources import map_source


def _source_stats(
    config: ProjectConfig,
) -> dict[str, tuple[int, int] | None]:
    snapshots: dict[str, tuple[int, int] | None] = {}
    for source in config.sources:
        try:
            stat = source.path.stat()
            snapshots[source.source_name] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            snapshots[source.source_name] = None
    return snapshots


def run_canonical(
    config_path: str | Path,
    *,
    inventory_path: str | Path | None = None,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Map all registered sources to streamed pre-ID Canonical DataFrames."""

    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    schema = load_canonical_schema(config.paths.canonical_schema)
    for source in config.sources:
        schema.frame_for(source.source_name)

    selected_inventory = (
        Path(inventory_path).expanduser().resolve(strict=False)
        if inventory_path is not None
        else find_latest_inventory(config.paths.metadata_dir)
    )
    inventory = load_inventory_records(selected_inventory, config)
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_3_canonical_schema.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info(
        "M1.3 canonical mapping started",
        extra={
            "inventory_path": str(selected_inventory),
            "schema_sha256": schema.sha256,
        },
    )
    source_stats_before = _source_stats(config)
    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)

    output_directory = (
        config.paths.output_root / "canonical" / metadata.run_id
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    frame_results = []
    for source in config.sources:
        spec = schema.frame_for(source.source_name)
        logger.info(
            "mapping source",
            extra={
                "source_name": source.source_name,
                "frame_name": spec.frame_name,
            },
        )
        result = map_source(
            source,
            spec,
            inventory[source.source_name],
            output_directory / f"{source.source_name}.parquet",
            schema_version=schema.schema_version,
        )
        frame_results.append(result)
        log_method = logger.info if result.valid else logger.error
        log_method(
            "source mapping completed",
            extra={
                "source_name": source.source_name,
                "rows": result.row_count,
                "valid": result.valid,
            },
        )

    run_result = CanonicalRunResult(
        run_id=metadata.run_id,
        schema_name=schema.schema_name,
        schema_version=schema.schema_version,
        schema_path=str(schema.path),
        schema_sha256=schema.sha256,
        inventory_path=str(selected_inventory),
        output_directory=str(output_directory),
        frames=tuple(frame_results),
    )
    artifacts = write_canonical_manifest(run_result, output_directory)
    source_stats_after = _source_stats(config)
    source_stat_changes = tuple(
        source.source_name
        for source in config.sources
        if source_stats_before[source.source_name]
        != source_stats_after[source.source_name]
    )
    reports = write_canonical_report(
        run_result,
        artifacts,
        config.paths.reports_dir,
        metadata,
        source_stat_changes=source_stat_changes,
        verification=verification,
    )
    status = (
        "complete"
        if run_result.schema_validation_passed and not source_stat_changes
        else "complete_with_validation_errors"
    )
    logger.log(
        logging.INFO if status == "complete" else logging.ERROR,
        "M1.3 canonical mapping completed",
        extra={
            "failure_count": run_result.failure_count,
            "mapped_source_count": run_result.mapped_source_count,
            "source_stat_changes": list(source_stat_changes),
            "status": status,
        },
    )
    return {
        "canonical_manifest_json": str(artifacts.manifest_json),
        "failure_count": run_result.failure_count,
        "json_report": str(reports.json),
        "mapped_source_count": run_result.mapped_source_count,
        "markdown_report": str(reports.markdown),
        "output_directory": str(output_directory),
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "schema_validation": (
            "PASS" if run_result.schema_validation_passed else "FAIL"
        ),
        "source_count": run_result.source_count,
        "source_stat_changes": list(source_stat_changes),
        "status": status,
    }
