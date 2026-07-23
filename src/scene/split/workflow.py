"""End-to-end permanent M1.6 Seoul district assignment."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import KST, collect_run_metadata
from scene.inventory.hashing import sha256_file
from scene.split.assign import build_assignment
from scene.split.balancing import prepare_balance_model, search_assignment
from scene.split.exceptions import DistrictAssignmentError
from scene.split.reporting import write_assignment_report
from scene.split.serialization import write_assignment_artifacts
from scene.split.statistics import (
    compute_balancing_statistics,
    load_canonical_districts,
)
from scene.split.validator import validate_assignment


def _input_paths(config: ProjectConfig) -> tuple[Path, ...]:
    assignment = config.district_assignment
    if assignment is None:
        raise DistrictAssignmentError(
            "district_assignment configuration is required"
        )
    return tuple(
        dict.fromkeys(
            (
                *(source.path for source in config.sources),
                assignment.canonical_boundary_path,
                assignment.building_geometry_path,
                assignment.road_geometry_path,
                assignment.poi_geometry_path,
                assignment.poi_attributes_path,
            )
        )
    )


def _snapshots(paths: tuple[Path, ...]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in paths:
        if not path.is_file():
            raise DistrictAssignmentError(f"required input is missing: {path}")
        stat = path.stat()
        result[str(path)] = {
            "file_size": stat.st_size,
            "modified_time_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
        }
    return result


def _run_assignment(
    config_path: str | Path,
    *,
    log_level: str,
    started_at: datetime,
    verification: Mapping[str, object] | None,
) -> dict[str, object]:
    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    assignment_config = config.district_assignment
    if assignment_config is None:
        raise DistrictAssignmentError(
            "district_assignment configuration is required"
        )
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_6_district_assignment.jsonl",
        metadata.run_id,
        level=log_level,
    )
    logger.info("M1.6 canonical boundary validation started")
    paths = _input_paths(config)
    before = _snapshots(paths)
    canonical = load_canonical_districts(assignment_config)

    logger.info("M1.6 balancing statistics started")
    statistics = compute_balancing_statistics(config, canonical)
    model = prepare_balance_model(
        statistics,
        canonical.districts,
        assignment_config,
    )
    logger.info("M1.6 deterministic constrained search started")
    search = search_assignment(statistics, model, assignment_config)
    assignment = build_assignment(
        canonical,
        model,
        search,
        assignment_config,
        run_id=metadata.run_id,
    )
    regenerated_search = search_assignment(
        statistics,
        model,
        assignment_config,
    )
    regenerated = build_assignment(
        canonical,
        model,
        regenerated_search,
        assignment_config,
        run_id=metadata.run_id,
    )
    validation = validate_assignment(
        assignment,
        assignment_config,
        regenerated_assignment=regenerated,
    )
    if not validation.valid:
        raise DistrictAssignmentError(
            f"district assignment validation failed: {validation.to_dict()}"
        )

    logger.info("M1.6 assignment serialization started")
    output = config.paths.output_root / "split" / metadata.run_id
    artifacts = write_assignment_artifacts(
        assignment,
        validation,
        statistics,
        model,
        assignment_config,
        output,
        config.paths.metadata_dir,
    )
    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    after = _snapshots(paths)
    changed = {
        path: {
            "before": before[path],
            "after": after[path],
        }
        for path in before
        if before[path] != after[path]
    }
    input_changes = {
        "changed_input_count": len(changed),
        "changed_inputs": changed,
        "source_unchanged": not changed,
        "canonical_boundary_unchanged": (
            before[str(assignment_config.canonical_boundary_path)]
            == after[str(assignment_config.canonical_boundary_path)]
        ),
    }
    if changed:
        raise DistrictAssignmentError(
            f"read-only input changed during M1.6: {sorted(changed)}"
        )
    reports = write_assignment_report(
        assignment,
        validation,
        statistics,
        model,
        artifacts,
        config.paths.reports_dir,
        metadata,
        input_changes=input_changes,
        verification=verification,
    )
    lists = {
        split: assignment.frame.loc[
            assignment.frame["split"] == split,
            "district_name",
        ].to_list()
        for split in ("train", "validation", "test")
    }
    logger.info(
        "M1.6 district assignment completed",
        extra={
            "assignment_hash": assignment.assignment_hash,
            "status": "complete",
        },
    )
    return {
        "assignment_hash": assignment.assignment_hash,
        "assignment_json": str(artifacts.assignment_json),
        "assignment_parquet": str(artifacts.assignment_parquet),
        "deterministic": "PASS",
        "failure_count": 0,
        "forbidden_artifact_count": 0,
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "run_id": metadata.run_id,
        "status": "complete",
        "test_count": validation.test_count,
        "test_districts": lists["test"],
        "train_count": validation.train_count,
        "train_districts": lists["train"],
        "validation": "PASS",
        "validation_count": validation.validation_count,
        "validation_districts": lists["validation"],
    }


def run_district_assignment(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run M1.6 and write a failure report before propagating fatal errors."""

    instant = started_at or datetime.now(tz=KST)
    try:
        return _run_assignment(
            config_path,
            log_level=log_level,
            started_at=instant,
            verification=verification,
        )
    except (
        DistrictAssignmentError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        config = load_config(config_path)
        metadata = collect_run_metadata(config, started_at=instant)
        output = config.paths.output_root / "split" / metadata.run_id
        generated = (
            [
                str(path)
                for path in sorted(output.rglob("*"))
                if path.is_file()
            ]
            if output.exists()
            else []
        )
        reports = write_reports(
            config.paths.reports_dir,
            f"{metadata.run_id}_m1_6_district_assignment",
            title="M1.6 District Assignment",
            metadata=metadata,
            summary={
                "failure_reason": str(exc),
                "generated_diagnostic_artifacts": generated,
                "status": "failed",
            },
            sections=(
                ReportSection(
                    "Failure",
                    f"- Failure: `{exc}`\n"
                    "- Assignment was not accepted and M1.7 is blocked.",
                ),
                ReportSection(
                    "Artifacts",
                    "\n".join(f"- `{path}`" for path in generated)
                    or "No split artifact was generated.",
                ),
            ),
        )
        raise DistrictAssignmentError(
            f"{exc}; failure report: {reports.markdown}"
        ) from exc
