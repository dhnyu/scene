"""End-to-end M1.8 candidate-only integration fixture workflow."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import pandas as pd
import pyogrio

from scene.core.config import ProjectConfig, load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import KST, RunMetadata, collect_run_metadata
from scene.inventory.hashing import sha256_file
from scene.miniature.candidate_query import (
    CandidateSource,
    link_raster_metadata,
    load_stable_id_lookup,
    query_candidates,
    read_candidate_source,
)
from scene.miniature.exceptions import MiniatureDatasetError
from scene.miniature.mapping import content_hash, provenance_frame
from scene.miniature.models import MiniatureDataset
from scene.miniature.reporting import write_miniature_report
from scene.miniature.selector import select_scenes
from scene.miniature.serialization import write_miniature_artifacts
from scene.miniature.validator import validate_dataset


def _snapshot(paths: tuple[Path, ...]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in dict.fromkeys(paths):
        if not path.is_file():
            raise MiniatureDatasetError(f"read-only input is missing: {path}")
        stat = path.stat()
        result[str(path)] = {
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
            "size": stat.st_size,
        }
    return result


def _candidate_sources(config: ProjectConfig) -> tuple[CandidateSource, ...]:
    district = config.district_assignment
    miniature = config.miniature_dataset
    if district is None or miniature is None:
        raise MiniatureDatasetError(
            "district_assignment and miniature_dataset config are required"
        )
    return (
        CandidateSource(
            "building",
            district.building_geometry_path,
            district.building_geometry_layer,
            "source_building_id",
            "building_id",
            frozenset({"Polygon", "MultiPolygon"}),
        ),
        CandidateSource(
            "road_link",
            district.road_geometry_path,
            district.road_geometry_layer,
            "source_link_id",
            "road_link_id",
            frozenset({"LineString", "MultiLineString"}),
        ),
        CandidateSource(
            "road_node",
            district.road_geometry_path,
            miniature.road_node_geometry_layer,
            "source_node_id",
            "road_node_id",
            frozenset({"Point"}),
        ),
        CandidateSource(
            "poi",
            district.poi_geometry_path,
            district.poi_geometry_layer,
            "source_poi_id",
            "poi_id",
            frozenset({"Point"}),
        ),
    )


def _load_scene_inputs(
    config: ProjectConfig,
) -> tuple[gpd.GeoDataFrame, dict[str, object], dict[str, object]]:
    miniature = config.miniature_dataset
    scene = config.scene_generation
    if miniature is None or scene is None:
        raise MiniatureDatasetError(
            "scene_generation and miniature_dataset config are required"
        )
    scenes = pyogrio.read_dataframe(
        miniature.scene_geometry_path,
        layer=miniature.scene_geometry_layer,
    )
    scene_summary = json.loads(
        miniature.scene_summary_path.read_text(encoding="utf-8")
    )
    assignment_lock = json.loads(
        scene.assignment_lock_path.read_text(encoding="utf-8")
    )
    selected = select_scenes(
        scenes,
        split_order=miniature.split_order,
        scenes_per_split=miniature.scenes_per_split,
    )
    assignment_values = set(selected["assignment_hash"].astype(str))
    if assignment_values != {str(assignment_lock["assignment_hash"])}:
        raise MiniatureDatasetError(
            "selected scenes do not match the M1.6 assignment lock"
        )
    versions = set(selected["scene_generation_version"].astype(str))
    if versions != {scene.scene_generation_version}:
        raise MiniatureDatasetError(
            "selected scenes have an unexpected generation version"
        )
    return selected, scene_summary, assignment_lock


def _assemble(
    config: ProjectConfig,
    metadata: RunMetadata,
    selected: gpd.GeoDataFrame,
    stable_ids: dict[str, dict[str, str]],
    *,
    scene_content_hash: str,
    assignment_lock: Mapping[str, object],
    canonical_boundary_hash: str,
) -> MiniatureDataset:
    district = config.district_assignment
    miniature = config.miniature_dataset
    scene = config.scene_generation
    if district is None or miniature is None or scene is None:
        raise MiniatureDatasetError("M1.8 configuration is incomplete")
    candidate_frames: dict[str, pd.DataFrame] = {}
    for source in _candidate_sources(config):
        source_frame = read_candidate_source(source, selected)
        candidate_frames[source.entity_type] = query_candidates(
            source_frame,
            selected,
            source_native_id_field=source.source_native_id_field,
            output_id_field=source.output_id_field,
            stable_ids=stable_ids[source.entity_type],
        )
    raster_sources = link_raster_metadata(
        selected,
        miniature.raster_metadata_path,
        landcover_source=district.landcover_source_name,
        dem_source=district.dem_source_name,
    )
    scene_attributes = pd.DataFrame(
        selected.drop(columns="geometry")
    ).sort_values(
        ["split", "grid_col", "grid_row", "scene_footprint_id"],
        kind="stable",
    ).reset_index(drop=True)
    digest = content_hash(scene_attributes, candidate_frames, raster_sources)
    provenance = provenance_frame(
        scene_ids=scene_attributes["scene_footprint_id"],
        assignment_hash=str(assignment_lock["assignment_hash"]),
        scene_content_hash=scene_content_hash,
        canonical_boundary_hash=canonical_boundary_hash,
        scene_generation_version=scene.scene_generation_version,
        run_id=metadata.run_id,
        config_hash=config.canonical_hash,
        miniature_content_hash=digest,
    )
    return MiniatureDataset(
        selected_scene_geometry=selected,
        scenes=scene_attributes,
        building_candidates=candidate_frames["building"],
        road_link_candidates=candidate_frames["road_link"],
        road_node_candidates=candidate_frames["road_node"],
        poi_candidates=candidate_frames["poi"],
        raster_sources=raster_sources,
        provenance=provenance,
        content_hash=digest,
    )


def _immutable_paths(config: ProjectConfig) -> tuple[Path, ...]:
    district = config.district_assignment
    miniature = config.miniature_dataset
    scene = config.scene_generation
    if district is None or miniature is None or scene is None:
        raise MiniatureDatasetError("M1.8 configuration is incomplete")
    return (
        config.paths.project_root / "study_methods.md",
        district.canonical_boundary_path,
        scene.assignment_lock_path,
        miniature.scene_geometry_path,
        miniature.scene_summary_path,
        miniature.stable_ids_path,
        miniature.raster_metadata_path,
        miniature.source_inventory_path,
        miniature.canonical_manifest_path,
        district.building_geometry_path,
        district.road_geometry_path,
        district.poi_geometry_path,
        *(source.path for source in config.sources),
    )


def _run(
    config_path: str | Path,
    *,
    started_at: datetime,
    log_level: str,
    verification: Mapping[str, object] | None,
) -> dict[str, object]:
    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    if (
        config.miniature_dataset is None
        or config.scene_generation is None
        or config.district_assignment is None
    ):
        raise MiniatureDatasetError(
            "miniature_dataset, scene_generation and district_assignment "
            "configuration are required"
        )
    metadata = collect_run_metadata(config, started_at=started_at)
    logger = configure_logging(
        config.paths.logs_dir / f"{metadata.run_id}_m1_8_miniature.jsonl",
        metadata.run_id,
        level=log_level,
    )
    before = _snapshot(_immutable_paths(config))
    logger.info("M1.8 input resolution started")
    selected, scene_summary, assignment_lock = _load_scene_inputs(config)
    raw_scene_hash = scene_summary.get("scene_content_hash")
    if not isinstance(raw_scene_hash, str) or len(raw_scene_hash) != 64:
        raise MiniatureDatasetError("M1.7 scene content hash is unavailable")
    stable_ids = {
        entity: load_stable_id_lookup(
            config.miniature_dataset.stable_ids_path,
            entity,
        )
        for entity in ("building", "road_link", "road_node", "poi")
    }
    canonical_boundary_hash = before[
        str(config.district_assignment.canonical_boundary_path)
    ]["sha256"]
    logger.info("M1.8 candidate query started")
    dataset = _assemble(
        config,
        metadata,
        selected,
        stable_ids,
        scene_content_hash=raw_scene_hash,
        assignment_lock=assignment_lock,
        canonical_boundary_hash=str(canonical_boundary_hash),
    )
    logger.info("M1.8 deterministic regeneration started")
    regenerated = _assemble(
        config,
        metadata,
        selected.sample(frac=1.0, random_state=20260723),
        stable_ids,
        scene_content_hash=raw_scene_hash,
        assignment_lock=assignment_lock,
        canonical_boundary_hash=str(canonical_boundary_hash),
    )
    known_ids = {
        entity: set(values.values())
        for entity, values in stable_ids.items()
    }
    validation = validate_dataset(
        dataset,
        split_order=config.miniature_dataset.split_order,
        scenes_per_split=config.miniature_dataset.scenes_per_split,
        known_ids=known_ids,
        regenerated=regenerated,
    )
    output = config.paths.output_root / "miniature" / metadata.run_id
    summary = {
        "assignment_hash": assignment_lock["assignment_hash"],
        "candidate_counts": {
            entity: len(frame)
            for entity, frame in dataset.candidate_frames().items()
        },
        "content_hash": dataset.content_hash,
        "miniature_version": config.miniature_dataset.miniature_version,
        "raster_metadata_count": len(dataset.raster_sources),
        "run_id": metadata.run_id,
        "scene_content_hash": raw_scene_hash,
        "scene_count": len(dataset.scenes),
        "scene_count_by_split": validation["scenes"]["scene_count_by_split"],
        "status": "complete",
    }
    artifacts = write_miniature_artifacts(
        dataset,
        validation,
        summary,
        output,
    )
    resolved = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved)
    after = _snapshot(_immutable_paths(config))
    changed = {
        path: {"after": after[path], "before": before[path]}
        for path in before
        if before[path] != after[path]
    }
    if changed:
        raise MiniatureDatasetError(
            f"read-only inputs changed during M1.8: {sorted(changed)}"
        )
    read_only = {
        "changed_input_count": len(changed),
        "inputs": after,
        "unchanged": not changed,
    }
    provenance_summary = {
        "assignment_hash": assignment_lock["assignment_hash"],
        "canonical_boundary_hash": canonical_boundary_hash,
        "config_hash": config.canonical_hash,
        "miniature_content_hash": dataset.content_hash,
        "run_id": metadata.run_id,
        "scene_content_hash": raw_scene_hash,
        "scene_generation_version": (
            config.scene_generation.scene_generation_version
        ),
    }
    reports = write_miniature_report(
        dataset,
        validation,
        artifacts,
        config.paths.reports_dir,
        metadata,
        provenance=provenance_summary,
        read_only=read_only,
        verification=dict(verification or {}),
    )
    logger.info("M1.8 completed")
    counts = summary["candidate_counts"]
    return {
        "candidate_counts": counts,
        "content_hash": dataset.content_hash,
        "deterministic": "PASS",
        "failure_count": 0,
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "output_directory": str(output),
        "raster_metadata_count": len(dataset.raster_sources),
        "run_id": metadata.run_id,
        "scene_count": len(dataset.scenes),
        "scene_count_by_split": summary["scene_count_by_split"],
        "status": "complete",
    }


def run_miniature(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Create the M1.8 dataset and a timestamp-matched failure report."""

    instant = started_at or datetime.now(tz=KST)
    try:
        return _run(
            config_path,
            started_at=instant,
            log_level=log_level,
            verification=verification,
        )
    except (OSError, RuntimeError, ValueError, MiniatureDatasetError) as exc:
        config = load_config(config_path)
        metadata = collect_run_metadata(config, started_at=instant)
        output = config.paths.output_root / "miniature" / metadata.run_id
        generated = (
            [str(path) for path in sorted(output.rglob("*")) if path.is_file()]
            if output.exists()
            else []
        )
        reports = write_reports(
            config.paths.reports_dir,
            f"{metadata.run_id}_m1_8_miniature_dataset",
            title="M1.8 Miniature Dataset",
            metadata=metadata,
            summary={
                "failure_count": 1,
                "failure_reason": str(exc),
                "generated_artifacts": generated,
                "status": "failed",
            },
            sections=(
                ReportSection(
                    "Failure",
                    f"- Blocked stage: M1.8 execution\n"
                    f"- Actual observation: `{exc}`\n"
                    f"- Preserved diagnostic artifacts: `{generated}`",
                ),
            ),
        )
        raise MiniatureDatasetError(
            f"{exc}; failure report: {reports.markdown}"
        ) from exc
