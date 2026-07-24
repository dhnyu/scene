"""Deterministic miniature content hashing and provenance."""

from __future__ import annotations

import json

import pandas as pd

from scene.id.generator import canonical_hash
from scene.miniature.models import MiniatureDataset


def content_hash(
    scenes: pd.DataFrame,
    candidates: dict[str, pd.DataFrame],
    raster_sources: pd.DataFrame,
) -> str:
    """Hash canonical tabular content while excluding run metadata."""

    records: list[str] = []
    scene_columns = (
        "scene_footprint_id",
        "split",
        "grid_col",
        "grid_row",
        "scene_generation_version",
        "assignment_hash",
    )
    for row in scenes.sort_values(
        ["split", "grid_col", "grid_row", "scene_footprint_id"],
        kind="stable",
    ).loc[:, scene_columns].itertuples(index=False, name=None):
        records.append(json.dumps(("scene", *row), separators=(",", ":")))
    for entity, frame in sorted(candidates.items()):
        id_column = next(
            column
            for column in frame.columns
            if column not in {"scene_footprint_id", "candidate_only"}
        )
        for row in frame.sort_values(
            ["scene_footprint_id", id_column],
            kind="stable",
        ).itertuples(index=False, name=None):
            records.append(
                json.dumps((entity, *row), separators=(",", ":"))
            )
    for row in raster_sources.sort_values(
        "scene_footprint_id",
        kind="stable",
    ).itertuples(index=False, name=None):
        records.append(json.dumps(("raster", *row), separators=(",", ":")))
    return canonical_hash("miniature-candidates-v1", *records)


def provenance_frame(
    *,
    scene_ids: pd.Series,
    assignment_hash: str,
    scene_content_hash: str,
    canonical_boundary_hash: str,
    scene_generation_version: str,
    run_id: str,
    config_hash: str,
    miniature_content_hash: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "scene_footprint_id": scene_ids.astype("string").to_list(),
            "assignment_hash": [assignment_hash] * len(scene_ids),
            "scene_content_hash": [scene_content_hash] * len(scene_ids),
            "canonical_boundary_hash": [canonical_boundary_hash]
            * len(scene_ids),
            "scene_generation_version": [scene_generation_version]
            * len(scene_ids),
            "run_id": [run_id] * len(scene_ids),
            "config_hash": [config_hash] * len(scene_ids),
            "miniature_content_hash": [miniature_content_hash]
            * len(scene_ids),
        }
    )
