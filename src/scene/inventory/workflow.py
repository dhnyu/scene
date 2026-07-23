"""End-to-end M1.2 workflow assembled from foundation services."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.inventory.registry import SourceRegistry
from scene.inventory.reporting import write_inventory_report
from scene.inventory.scanner import scan_inventory
from scene.inventory.serialization import write_inventory


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


def run_inventory(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
) -> dict[str, object]:
    """Register, scan, validate, serialize, and report every configured source."""

    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_2_source_inventory.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.2 source inventory started")
    source_stats_before = _source_stats(config)

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)

    registry = SourceRegistry.from_project_config(config)
    scan = scan_inventory(
        registry,
        run_id=metadata.run_id,
        started_at_kst=metadata.started_at_kst,
        logger=logger,
    )
    inventory_paths = write_inventory(
        scan,
        config.paths.metadata_dir / "inventory",
    )
    source_stats_after = _source_stats(config)
    source_stat_changes = tuple(
        source.source_name
        for source in config.sources
        if source_stats_before[source.source_name]
        != source_stats_after[source.source_name]
    )
    report_paths = write_inventory_report(
        scan,
        inventory_paths,
        config.paths.reports_dir,
        metadata,
        source_stat_changes=source_stat_changes,
    )
    logger.info(
        "M1.2 source inventory completed: sources=%d failures=%d",
        scan.source_count,
        scan.failure_count,
    )
    return {
        "failure_count": scan.failure_count,
        "inventory_json": str(inventory_paths.json),
        "inventory_parquet": str(inventory_paths.parquet),
        "json_report": str(report_paths.json),
        "markdown_report": str(report_paths.markdown),
        "resolved_config": str(resolved_path),
        "run_id": scan.run_id,
        "source_count": scan.source_count,
        "source_stat_changes": list(source_stat_changes),
        "status": (
            "complete"
            if scan.failure_count == 0
            else "complete_with_validation_errors"
        ),
    }
