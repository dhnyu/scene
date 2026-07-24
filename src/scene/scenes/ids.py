"""Approved scene grid-key ID materialization."""

from __future__ import annotations

import geopandas as gpd

from scene.core.config import SceneGenerationConfig
from scene.id.generator import DerivedIdFactory


def add_scene_ids(
    scenes: gpd.GeoDataFrame,
    config: SceneGenerationConfig,
) -> gpd.GeoDataFrame:
    result = scenes.copy()
    result["scene_footprint_id"] = [
        DerivedIdFactory.scene_footprint_id(
            config.scene_generation_version,
            config.canonical_crs,
            config.origin_x_m,
            config.origin_y_m,
            config.scene_width_m,
            config.scene_height_m,
            config.stride_x_m,
            config.stride_y_m,
            int(col),
            int(row),
        )
        for col, row in zip(
            result["grid_col"],
            result["grid_row"],
            strict=True,
        )
    ]
    return result
