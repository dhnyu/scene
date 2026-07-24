from __future__ import annotations

from dataclasses import replace

from scene.core.config import load_config
from scene.scenes.grid import (
    generate_candidate_grid,
    grid_center,
    grid_index_bounds,
)


def test_center_anchor_and_signed_indices(project_config_path) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    assert grid_center(0, 0, config) == (0.0, 0.0)
    assert grid_center(1, 0, config) == (250.0, 0.0)
    assert grid_center(-1, 0, config) == (-250.0, 0.0)


def test_exact_square_and_deterministic_index_range(project_config_path) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    bounds = (-500.0, -500.0, 750.0, 750.0)
    first = generate_candidate_grid(bounds, config)
    second = generate_candidate_grid(bounds, config)
    assert grid_index_bounds(bounds, config) == (-1, 2, -1, 2)
    assert first[["grid_col", "grid_row"]].equals(
        second[["grid_col", "grid_row"]]
    )
    assert set(first.geometry.area) == {250_000.0}
    assert set(first.geometry.bounds.maxx - first.geometry.bounds.minx) == {
        500.0
    }
    origin = first.loc[
        (first.grid_col == 0) & (first.grid_row == 0)
    ].iloc[0]
    assert tuple(origin.geometry.bounds) == (-250.0, -250.0, 250.0, 250.0)
