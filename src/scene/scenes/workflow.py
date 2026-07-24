"""End-to-end read-only-input M1.7 scene-footprint workflow."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

import pyarrow.parquet as pq

from scene.core.config import load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import KST, collect_run_metadata
from scene.inventory.hashing import sha256_file
from scene.scenes.exceptions import SceneFootprintError
from scene.scenes.generator import generate_scene_footprints
from scene.scenes.reporting import write_scene_report
from scene.scenes.serialization import write_scene_artifacts
from scene.scenes.statistics import scene_statistics
from scene.scenes.validator import validate_scene_result


def _latest(root: Path, pattern: str) -> Path:
    matches = sorted(
        (path for path in root.rglob(pattern) if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
    )
    if not matches:
        raise SceneFootprintError(f"required manifest not found: {pattern}")
    return matches[-1]


def _snapshot(paths: tuple[Path, ...]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in paths:
        if not path.is_file():
            raise SceneFootprintError(f"read-only input is missing: {path}")
        stat = path.stat()
        result[str(path)] = {
            "sha256": sha256_file(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def _previous_scene_comparison(
    output_root: Path,
    report_root: Path,
    *,
    new_scene_ids: set[str],
    new_content_hash: str,
) -> dict[str, object]:
    candidates = sorted(
        path
        for path in (output_root / "scenes").glob(
            "*/scene_footprints.parquet"
        )
        if path.is_file()
    )
    if not candidates:
        raise SceneFootprintError(
            "superseded M1.7 scene artifact is missing"
        )
    previous = candidates[-1]
    previous_run_id = previous.parent.name
    old_ids = set(
        pq.read_table(
            previous,
            columns=["scene_footprint_id"],
        )["scene_footprint_id"].to_pylist()
    )
    report_candidates = sorted(
        report_root.glob(f"{previous_run_id}_m1_7*.json")
    )
    if not report_candidates:
        raise SceneFootprintError(
            f"superseded M1.7 report is missing: {previous_run_id}"
        )
    old_report = json.loads(
        report_candidates[-1].read_text(encoding="utf-8")
    )
    old_hash = old_report["summary"].get("scene_content_hash")
    if not isinstance(old_hash, str):
        raise SceneFootprintError(
            "superseded M1.7 content hash is missing"
        )
    return {
        "added_scene_id_count": len(new_scene_ids - old_ids),
        "changed_scene_id_count": len(new_scene_ids ^ old_ids),
        "content_hash_changed": old_hash != new_content_hash,
        "new_content_hash": new_content_hash,
        "new_scene_count": len(new_scene_ids),
        "old_content_hash": old_hash,
        "old_scene_count": len(old_ids),
        "removed_scene_id_count": len(old_ids - new_scene_ids),
        "scene_count_delta": len(new_scene_ids) - len(old_ids),
        "superseded_run_id": previous_run_id,
        "superseded_status": "superseded",
    }


def _run(
    config_path: str | Path,
    *,
    log_level: str,
    started_at: datetime,
    verification: Mapping[str, object] | None,
) -> dict[str, object]:
    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    if config.district_assignment is None or config.scene_generation is None:
        raise SceneFootprintError(
            "district_assignment and scene_generation configuration are required"
        )
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir
        / f"{metadata.run_id}_m1_7_scene_footprints.jsonl",
        metadata.run_id,
        level=log_level,
    )
    inventory_manifest = _latest(
        config.paths.output_root / "inventory",
        "*_source_inventory.json",
    )
    canonical_manifest = _latest(
        config.paths.output_root / "canonical",
        "*_canonical_manifest.json",
    )
    official_source = next(
        (
            source.path
            for source in config.sources
            if source.source_name == "koreanadm_2024q2_sigungu"
        ),
        None,
    )
    if official_source is None:
        raise SceneFootprintError(
            "registered koreanadm_2024q2_sigungu source is missing"
        )
    immutable_inputs = (
        config.paths.project_root / "study_methods.md",
        official_source,
        config.district_assignment.canonical_boundary_path,
        config.scene_generation.assignment_lock_path,
        inventory_manifest,
        canonical_manifest,
    )
    before = _snapshot(immutable_inputs)
    logger.info("M1.7 pure generation started")
    result = generate_scene_footprints(config)
    logger.info("M1.7 deterministic regeneration started")
    regenerated = generate_scene_footprints(config)
    validation = validate_scene_result(
        result,
        config,
        regenerated=regenerated,
    )
    statistics = scene_statistics(result, config)
    comparison = _previous_scene_comparison(
        config.paths.output_root,
        config.paths.reports_dir,
        new_scene_ids=set(result.scenes["scene_footprint_id"]),
        new_content_hash=result.content_hash,
    )
    statistics["comparison_to_superseded_run"] = comparison
    statistics["run_metadata"] = metadata.to_dict()
    statistics["scene_content_hash"] = result.content_hash
    output = config.paths.output_root / "scenes" / metadata.run_id
    artifacts = write_scene_artifacts(
        result,
        validation,
        statistics,
        output,
        metadata,
        canonical_boundary_hash=before[
            str(config.district_assignment.canonical_boundary_path)
        ]["sha256"],
    )
    resolved = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved)
    after = _snapshot(immutable_inputs)
    changed = {
        path: {"before": before[path], "after": after[path]}
        for path in before
        if before[path] != after[path]
    }
    if changed:
        raise SceneFootprintError(
            f"read-only input changed during M1.7: {sorted(changed)}"
        )
    read_only = {
        "changed_input_count": len(changed),
        "inputs": after,
        "unchanged": not changed,
    }
    verify = dict(verification or {})
    reports = write_scene_report(
        result,
        validation,
        statistics,
        artifacts,
        config.paths.reports_dir,
        metadata,
        comparison=comparison,
        input_resolution={
            "assignment_hash": result.assignment_lock["assignment_hash"],
            "assignment_lock": str(config.scene_generation.assignment_lock_path),
            "canonical_boundary": str(
                config.district_assignment.canonical_boundary_path
            ),
            "canonical_boundary_layer": (
                config.district_assignment.canonical_boundary_layer
            ),
            "canonical_crs": config.scene_generation.canonical_crs,
            "inventory_manifest": str(inventory_manifest),
            "canonical_manifest": str(canonical_manifest),
            "scene_generation_config": config.scene_generation.to_dict(),
        },
        read_only=read_only,
        verification=verify,
    )
    counts = validation["split_and_leakage"]["scene_count_by_split"]
    minimum_distance = min(
        float(row["allowable_region_distance_m"])
        for row in result.allowable_regions.pair_audit
    )
    logger.info("M1.7 completed")
    return {
        "assignment_hash": result.assignment_lock["assignment_hash"],
        "candidate_scene_count": result.eligibility.candidate_count,
        "content_hash": result.content_hash,
        "comparison": comparison,
        "deterministic": "PASS",
        "duplicate_scene_id_count": 0,
        "failure_count": 0,
        "forbidden_artifact_count": 0,
        "invalid_geometry_count": 0,
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "minimum_allowable_region_distance_m": minimum_distance,
        "other_split_district_mapping_count": 0,
        "output_directory": str(output),
        "rejected_scene_count": result.eligibility.rejected_count,
        "run_id": metadata.run_id,
        "scene_count_by_split": counts,
        "status": "complete",
        "validation": "PASS",
    }


def run_scene_footprints(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    instant = started_at or datetime.now(tz=KST)
    try:
        return _run(
            config_path,
            log_level=log_level,
            started_at=instant,
            verification=verification,
        )
    except (OSError, RuntimeError, ValueError, SceneFootprintError) as exc:
        config = load_config(config_path)
        metadata = collect_run_metadata(config, started_at=instant)
        output = config.paths.output_root / "scenes" / metadata.run_id
        generated = (
            [str(path) for path in sorted(output.rglob("*")) if path.is_file()]
            if output.exists()
            else []
        )
        reports = write_reports(
            config.paths.reports_dir,
            f"{metadata.run_id}_m1_7_recovery_strict_d019",
            title="M1.7 Recovery - Strict D-019 Scene Regeneration",
            metadata=metadata,
            summary={
                "blocked_stage": "M1.7 execution",
                "failure_reason": str(exc),
                "generated_artifacts": generated,
                "m1_8_allowed": False,
                "status": "failed",
            },
            sections=(
                ReportSection(
                    "Failure",
                    f"- Actual observation: `{exc}`\n"
                    "- M1.8 cannot proceed.\n"
                    f"- Preserved diagnostic artifacts: `{generated}`",
                ),
            ),
        )
        raise SceneFootprintError(
            f"{exc}; failure report: {reports.markdown}"
        ) from exc
