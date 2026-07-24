from __future__ import annotations

import geopandas as gpd
from shapely import box

from scene.core.config import load_config
from scene.scenes.district_mapping import build_district_mapping


def _scene() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "scene_footprint_id": ["scene"],
            "split": ["train"],
        },
        geometry=[box(0, 0, 500, 500)],
        crs="EPSG:5186",
    )


def test_largest_intersection_is_primary(project_config_path) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    districts = gpd.GeoDataFrame(
        {
            "district_id": ["a", "b"],
            "district_code": ["01", "02"],
            "district_name": ["A", "B"],
            "split": ["train", "train"],
        },
        geometry=[box(0, 0, 300, 500), box(300, 0, 500, 500)],
        crs="EPSG:5186",
    )
    mapping = build_district_mapping(_scene(), districts, config)
    assert mapping.loc[mapping.is_primary_district, "district_code"].item() == "01"
    assert mapping["intersection_fraction"].sum() == 1.0


def test_exact_tie_uses_district_code_and_touch_is_excluded(
    project_config_path,
) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    districts = gpd.GeoDataFrame(
        {
            "district_id": ["b", "a", "touch"],
            "district_code": ["02", "01", "00"],
            "district_name": ["B", "A", "Touch"],
            "split": ["train", "train", "train"],
        },
        geometry=[
            box(250, 0, 500, 500),
            box(0, 0, 250, 500),
            box(500, 0, 700, 500),
        ],
        crs="EPSG:5186",
    )
    mapping = build_district_mapping(_scene(), districts, config)
    assert set(mapping["district_code"]) == {"01", "02"}
    assert mapping.loc[mapping.is_primary_district, "district_code"].item() == "01"
