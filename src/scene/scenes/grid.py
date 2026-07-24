"""Deterministic center-anchored scene grid construction."""

from __future__ import annotations

import math
from typing import Iterator

import geopandas as gpd
import numpy as np
from shapely import box

from scene.core.config import SceneGenerationConfig


def grid_center(
    grid_col: int,
    grid_row: int,
    config: SceneGenerationConfig,
) -> tuple[float, float]:
    return (
        config.origin_x_m + grid_col * config.stride_x_m,
        config.origin_y_m + grid_row * config.stride_y_m,
    )


def grid_index_bounds(
    bounds: tuple[float, float, float, float],
    config: SceneGenerationConfig,
) -> tuple[int, int, int, int]:
    min_x, min_y, max_x, max_y = bounds
    half_width = config.scene_width_m / 2.0
    half_height = config.scene_height_m / 2.0
    return (
        math.ceil(
            (min_x + half_width - config.origin_x_m)
            / config.stride_x_m
        ),
        math.floor(
            (max_x - half_width - config.origin_x_m)
            / config.stride_x_m
        ),
        math.ceil(
            (min_y + half_height - config.origin_y_m)
            / config.stride_y_m
        ),
        math.floor(
            (max_y - half_height - config.origin_y_m)
            / config.stride_y_m
        ),
    )


def iter_grid_indices(
    index_bounds: tuple[int, int, int, int],
) -> Iterator[tuple[int, int]]:
    first_col, last_col, first_row, last_row = index_bounds
    for row in range(first_row, last_row + 1):
        for col in range(first_col, last_col + 1):
            yield col, row


def generate_candidate_grid(
    bounds: tuple[float, float, float, float],
    config: SceneGenerationConfig,
) -> gpd.GeoDataFrame:
    index_bounds = grid_index_bounds(bounds, config)
    indices = np.asarray(tuple(iter_grid_indices(index_bounds)), dtype=np.int64)
    if indices.size == 0:
        return gpd.GeoDataFrame(
            {"grid_col": [], "grid_row": []},
            geometry=[],
            crs=config.canonical_crs,
        )
    cols = indices[:, 0]
    rows = indices[:, 1]
    centers_x = config.origin_x_m + cols * config.stride_x_m
    centers_y = config.origin_y_m + rows * config.stride_y_m
    half_width = config.scene_width_m / 2.0
    half_height = config.scene_height_m / 2.0
    geometries = box(
        centers_x - half_width,
        centers_y - half_height,
        centers_x + half_width,
        centers_y + half_height,
    )
    return gpd.GeoDataFrame(
        {
            "grid_col": cols,
            "grid_row": rows,
            "centroid_x": centers_x,
            "centroid_y": centers_y,
        },
        geometry=geometries,
        crs=config.canonical_crs,
    )
