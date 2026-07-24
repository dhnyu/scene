"""Pure M1.7 scene-footprint generation from frozen inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from scene.core.config import ProjectConfig
from scene.id.generator import canonical_hash
from scene.scenes.allowable_region import (
    allowable_geodataframe,
    build_allowable_regions,
)
from scene.scenes.district_mapping import build_district_mapping
from scene.scenes.eligibility import select_eligible_scenes
from scene.scenes.exceptions import SceneFootprintError
from scene.scenes.grid import generate_candidate_grid
from scene.scenes.ids import add_scene_ids
from scene.scenes.models import SceneGenerationResult
from scene.split.assign import assignment_content_hash
from scene.split.statistics import load_canonical_districts


def canonical_json_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_assignment_lock(
    path: Path,
    districts: gpd.GeoDataFrame,
    *,
    expected_version: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise SceneFootprintError(f"assignment lock is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "assignment",
        "assignment_config_hash",
        "assignment_hash",
        "assignment_seed",
        "assignment_version",
        "canonical_boundary_content_hash",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise SceneFootprintError(
            f"assignment lock fields are missing: {missing}"
        )
    if payload["assignment_version"] != expected_version:
        raise SceneFootprintError("assignment lock version mismatch")
    rows = payload["assignment"]
    if not isinstance(rows, list) or len(rows) != 25:
        raise SceneFootprintError("assignment lock must contain 25 districts")
    frame = pd.DataFrame(rows)
    if set(frame.columns) != {"district_code", "district_id", "split"}:
        raise SceneFootprintError("assignment lock row schema is invalid")
    counts = frame["split"].value_counts().to_dict()
    if counts != {"train": 15, "validation": 5, "test": 5}:
        raise SceneFootprintError(f"assignment lock counts are invalid: {counts}")
    if frame["district_id"].duplicated().any() or frame[
        "district_code"
    ].duplicated().any():
        raise SceneFootprintError("assignment lock contains duplicate districts")
    expected_pairs = set(
        zip(
            districts["district_id"].astype(str),
            districts["district_code"].astype(str),
            strict=True,
        )
    )
    lock_pairs = set(
        zip(
            frame["district_id"].astype(str),
            frame["district_code"].astype(str),
            strict=True,
        )
    )
    if expected_pairs != lock_pairs:
        raise SceneFootprintError(
            "assignment lock does not match canonical district IDs and codes"
        )
    calculated = assignment_content_hash(
        [
            (
                str(row.district_id),
                str(row.district_code),
                str(row.split),
            )
            for row in frame.itertuples()
        ]
    )
    if calculated != payload["assignment_hash"]:
        raise SceneFootprintError(
            f"assignment hash mismatch: {calculated} != "
            f"{payload['assignment_hash']}"
        )
    return payload


def _scene_content_hash(
    scenes: gpd.GeoDataFrame,
    mapping: pd.DataFrame,
) -> str:
    scene_rows = []
    for row in scenes.sort_values("scene_footprint_id").itertuples():
        scene_rows.append(
            [
                row.scene_footprint_id,
                row.split,
                int(row.grid_col),
                int(row.grid_row),
                *(format(float(value), ".12f") for value in row.geometry.bounds),
            ]
        )
    mapping_rows = [
        [
            str(row.scene_footprint_id),
            str(row.district_id),
            format(float(row.intersection_area_m2), ".9f"),
            bool(row.is_primary_district),
        ]
        for row in mapping.sort_values(
            ["scene_footprint_id", "district_code"]
        ).itertuples()
    ]
    return canonical_json_hash(
        {"district_mapping": mapping_rows, "scenes": scene_rows}
    )


def generate_scene_footprints(config: ProjectConfig) -> SceneGenerationResult:
    assignment_config = config.district_assignment
    scene_config = config.scene_generation
    if assignment_config is None or scene_config is None:
        raise SceneFootprintError(
            "district_assignment and scene_generation configuration are required"
        )
    canonical = load_canonical_districts(assignment_config)
    districts = canonical.districts.copy()
    lock = load_assignment_lock(
        scene_config.assignment_lock_path,
        districts,
        expected_version=assignment_config.assignment_version,
    )
    if (
        lock["canonical_boundary_content_hash"]
        != canonical.content_hash
    ):
        raise SceneFootprintError(
            "assignment lock canonical boundary content hash mismatch"
        )
    assignments = {
        str(row["district_id"]): str(row["split"])
        for row in lock["assignment"]
    }
    districts["split"] = districts["district_id"].map(assignments)
    if districts["split"].isna().any():
        raise SceneFootprintError("canonical district is not assigned")

    regions = build_allowable_regions(districts, scene_config)
    study_bounds = tuple(
        map(float, gpd.GeoSeries(list(regions.raw_unions.values())).total_bounds)
    )
    candidates = generate_candidate_grid(study_bounds, scene_config)
    eligibility = select_eligible_scenes(candidates, regions)
    scenes = add_scene_ids(eligibility.eligible, scene_config)
    scene_config_hash = canonical_json_hash(scene_config.to_dict())
    scene_area = scene_config.scene_width_m * scene_config.scene_height_m
    bounds = scenes.geometry.bounds
    scenes["scene_id"] = scenes["scene_footprint_id"]
    scenes["scene_generation_version"] = (
        scene_config.scene_generation_version
    )
    scenes["xmin"] = bounds["minx"].to_numpy()
    scenes["ymin"] = bounds["miny"].to_numpy()
    scenes["xmax"] = bounds["maxx"].to_numpy()
    scenes["ymax"] = bounds["maxy"].to_numpy()
    scenes["width_m"] = scene_config.scene_width_m
    scenes["height_m"] = scene_config.scene_height_m
    scenes["area_m2"] = scene_area
    scenes["crs"] = scene_config.canonical_crs
    scenes["assignment_version"] = lock["assignment_version"]
    scenes["assignment_seed"] = int(lock["assignment_seed"])
    scenes["assignment_hash"] = lock["assignment_hash"]
    scenes["assignment_config_hash"] = lock["assignment_config_hash"]
    scenes["scene_generation_config_hash"] = scene_config_hash
    scenes["processing_block_id"] = pd.NA

    mapping = build_district_mapping(scenes, districts, scene_config)
    primary = mapping.loc[mapping["is_primary_district"]].set_index(
        "scene_footprint_id"
    )
    scenes["district_id"] = scenes["scene_footprint_id"].map(
        primary["district_id"]
    )
    scenes["district_name"] = scenes["scene_footprint_id"].map(
        primary["district_name"]
    )
    ordered = [
        "scene_footprint_id",
        "scene_id",
        "scene_generation_version",
        "split",
        "grid_col",
        "grid_row",
        "xmin",
        "ymin",
        "xmax",
        "ymax",
        "centroid_x",
        "centroid_y",
        "width_m",
        "height_m",
        "area_m2",
        "crs",
        "district_id",
        "district_name",
        "assignment_version",
        "assignment_seed",
        "assignment_hash",
        "assignment_config_hash",
        "scene_generation_config_hash",
        "processing_block_id",
        "geometry",
    ]
    scenes = scenes.loc[:, ordered]
    content_hash = _scene_content_hash(scenes, mapping)
    return SceneGenerationResult(
        scenes=scenes,
        districts=districts,
        district_mapping=mapping,
        allowable_regions=regions,
        allowable_frame=allowable_geodataframe(regions, scene_config),
        eligibility=eligibility,
        assignment_lock=lock,
        scene_generation_config_hash=scene_config_hash,
        content_hash=content_hash,
    )
