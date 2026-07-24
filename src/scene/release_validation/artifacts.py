"""Resolve complete reference and replay artifact chains."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from scene.core.config import ProjectConfig
from scene.release_validation.exceptions import ReleaseValidationError
from scene.release_validation.models import ReleaseArtifacts


def _complete_miniature(root: Path, *, config_hash: str) -> Path:
    required = {
        "miniature_scene.parquet",
        "provenance.parquet",
        "scene_building_candidates.parquet",
        "scene_poi_candidates.parquet",
        "scene_raster_sources.parquet",
        "scene_road_link_candidates.parquet",
        "scene_road_node_candidates.parquet",
        "summary.json",
        "validation.json",
    }
    for directory in sorted(root.glob("*"), reverse=True):
        if directory.is_dir() and all(
            (directory / name).is_file() for name in required
        ):
            summary = json.loads(
                (directory / "summary.json").read_text(encoding="utf-8")
            )
            provenance = directory / "provenance.parquet"
            if not provenance.is_file():
                continue
            hashes = set(
                pq.read_table(
                    provenance,
                    columns=["config_hash"],
                )["config_hash"].to_pylist()
            )
            if summary.get("status") == "complete" and hashes == {config_hash}:
                return directory
    raise ReleaseValidationError("no complete M1.8 artifact set found")


def _locked_split_directory(config: ProjectConfig) -> Path:
    lock = json.loads(
        config.scene_generation.assignment_lock_path.read_text(encoding="utf-8")
    )
    for directory in sorted((config.paths.output_root / "split").glob("*")):
        summary_path = directory / "assignment_summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("assignment_hash") == lock["assignment_hash"]:
            return directory
    raise ReleaseValidationError("no artifact matches the assignment lock")


def resolve_reference_artifacts(config: ProjectConfig) -> ReleaseArtifacts:
    """Resolve the approved pre-M1.9 chain before replay creates new runs."""

    miniature = config.miniature_dataset
    if miniature is None:
        raise ReleaseValidationError("miniature_dataset config is required")
    return ReleaseArtifacts(
        inventory_json=miniature.source_inventory_path,
        canonical_manifest=miniature.canonical_manifest_path,
        building_directory=config.district_assignment.building_geometry_path.parent,
        road_directory=config.district_assignment.road_geometry_path.parent,
        poi_directory=config.district_assignment.poi_geometry_path.parent,
        raster_directory=miniature.raster_metadata_path.parent,
        ids_directory=miniature.stable_ids_path.parent,
        boundary_directory=config.district_assignment.canonical_boundary_path.parent,
        split_directory=_locked_split_directory(config),
        scene_directory=miniature.scene_geometry_path.parent,
        miniature_directory=_complete_miniature(
            config.paths.output_root / "miniature",
            config_hash=config.canonical_hash,
        ),
    )
