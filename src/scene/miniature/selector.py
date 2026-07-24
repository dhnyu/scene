"""Deterministic scene selection for M1.8."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from scene.miniature.exceptions import MiniatureDatasetError


SCENE_REQUIRED_COLUMNS = {
    "assignment_hash",
    "assignment_version",
    "grid_col",
    "grid_row",
    "scene_footprint_id",
    "scene_generation_version",
    "split",
}


def select_scenes(
    scenes: gpd.GeoDataFrame,
    *,
    split_order: tuple[str, ...],
    scenes_per_split: int,
) -> gpd.GeoDataFrame:
    """Select the first contracted grid keys within every split."""

    missing = sorted(SCENE_REQUIRED_COLUMNS - set(scenes.columns))
    if missing:
        raise MiniatureDatasetError(
            f"scene footprint input is missing columns: {', '.join(missing)}"
        )
    if scenes.crs is None or scenes.crs.to_epsg() != 5186:
        raise MiniatureDatasetError("scene footprint CRS must be EPSG:5186")
    selected: list[gpd.GeoDataFrame] = []
    for split in split_order:
        group = scenes.loc[scenes["split"].astype(str) == split].sort_values(
            ["grid_col", "grid_row", "scene_footprint_id"],
            kind="stable",
        )
        if len(group) < scenes_per_split:
            raise MiniatureDatasetError(
                f"split {split!r} has only {len(group)} scenes"
            )
        selected.append(group.iloc[:scenes_per_split].copy())
    result = gpd.GeoDataFrame(
        pd.concat(selected, ignore_index=True),
        geometry="geometry",
        crs=scenes.crs,
    )
    if result["scene_footprint_id"].duplicated().any():
        raise MiniatureDatasetError("selected scene IDs are not unique")
    return result
