"""End-to-end M1.5 Stable IDs workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.run_context import collect_run_metadata
from scene.id.generator import StableIdGenerator
from scene.id.reader import StableIdReader, find_latest_canonical_manifest
from scene.id.reporting import write_stable_id_report
from scene.id.serialization import StableIdSerializer
from scene.id.validator import StableIdValidator
from scene.schema.schema import load_canonical_schema


def _stats(paths: tuple[Path, ...]) -> dict[str, tuple[int, int] | None]:
    values: dict[str, tuple[int, int] | None] = {}
    for path in paths:
        try:
            stat = path.stat()
            values[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            values[str(path)] = None
    return values


def _source_paths(config: ProjectConfig) -> tuple[Path, ...]:
    return tuple(source.path for source in config.sources)


def run_stable_ids(
    config_path: str | Path,
    *,
    canonical_manifest: str | Path | None = None,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Generate, validate, and serialize the M1.5 ID registry."""

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
        config.paths.logs_dir / f"{metadata.run_id}_m1_5_stable_ids.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.5 Stable IDs started")

    source_stats_before = _stats(_source_paths(config))
    reader = StableIdReader(schema, config.paths.output_root)
    source = reader.read(manifest_path)
    canonical_paths = (
        source.canonical_manifest_path,
        *(frame.path for frame in source.frames),
    )
    canonical_stats_before = _stats(canonical_paths)

    generator = StableIdGenerator()
    dataset = generator.generate(
        source,
        run_id=metadata.run_id,
        config_hash=metadata.resolved_config_hash,
    )
    regeneration_digest = generator.regeneration_digest(
        source,
        run_id=metadata.run_id,
        config_hash=metadata.resolved_config_hash,
    )
    validation = StableIdValidator().validate(
        dataset,
        regeneration_digest=regeneration_digest,
    )
    logger.info(
        "Stable ID validation completed",
        extra={
            "row_count": dataset.row_count,
            "valid": validation.valid,
        },
    )

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    artifacts = StableIdSerializer().serialize(
        dataset,
        validation,
        config.paths.output_root / "ids" / metadata.run_id,
        run_id=metadata.run_id,
        config_hash=metadata.resolved_config_hash,
    )

    before = {**source_stats_before, **canonical_stats_before}
    after = {
        **_stats(_source_paths(config)),
        **_stats(canonical_paths),
    }
    input_stat_changes = tuple(
        path for path, value in before.items() if value != after[path]
    )
    reports = write_stable_id_report(
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
    logger.info("M1.5 Stable IDs completed", extra={"status": status})
    counts = validation.counts
    return {
        "building_id_count": counts["building"],
        "failure_count": (
            validation.global_duplicate_id_count
            + validation.global_null_id_count
            + validation.provenance_missing_count
            + (0 if validation.deterministic_regeneration else 1)
            + (0 if validation.source_canonical_mapping_valid else 1)
            + len(input_stat_changes)
        ),
        "ids_json": str(artifacts.ids_json),
        "ids_parquet": str(artifacts.ids_parquet),
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "poi_id_count": counts["poi"],
        "provenance_parquet": str(artifacts.provenance_parquet),
        "resolved_config": str(resolved_path),
        "road_link_id_count": counts["road_link"],
        "road_node_id_count": counts["road_node"],
        "run_id": metadata.run_id,
        "status": status,
        "validation": "PASS" if validation.valid else "FAIL",
    }
