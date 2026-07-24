"""Positive-area scene-to-district mapping and primary ownership."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from scene.core.config import SceneGenerationConfig
from scene.scenes.exceptions import SceneFootprintError


MAPPING_COLUMNS = (
    "scene_footprint_id",
    "district_id",
    "district_code",
    "district_name",
    "split",
    "intersection_area_m2",
    "intersection_fraction",
    "is_primary_district",
)


def build_district_mapping(
    scenes: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    config: SceneGenerationConfig,
) -> pd.DataFrame:
    candidates = gpd.sjoin(
        scenes[["scene_footprint_id", "split", "geometry"]],
        districts[
            ["district_id", "district_code", "district_name", "split", "geometry"]
        ].rename(columns={"split": "district_split"}),
        predicate="intersects",
        how="inner",
    )
    if candidates.empty:
        raise SceneFootprintError("no scene-district intersections found")
    district_geometry = gpd.GeoSeries(
        districts.geometry.iloc[
            candidates["index_right"].astype(int).to_numpy()
        ].array,
        index=candidates.index,
        crs=districts.crs,
    )
    candidates["intersection_area_m2"] = candidates.geometry.intersection(
        district_geometry,
        align=False,
    ).area
    positive = candidates.loc[candidates["intersection_area_m2"] > 0.0].copy()
    other_split = positive["split"] != positive["district_split"]
    if other_split.any():
        raise SceneFootprintError(
            f"{int(other_split.sum())} other-split district mappings found"
        )
    positive["intersection_fraction"] = (
        positive["intersection_area_m2"]
        / (config.scene_width_m * config.scene_height_m)
    )
    positive.sort_values(
        [
            "scene_footprint_id",
            "intersection_area_m2",
            "district_code",
        ],
        ascending=[True, False, True],
        kind="mergesort",
        inplace=True,
    )
    positive["is_primary_district"] = ~positive[
        "scene_footprint_id"
    ].duplicated()
    result = positive.loc[
        :,
        [
            "scene_footprint_id",
            "district_id",
            "district_code",
            "district_name",
            "split",
            "intersection_area_m2",
            "intersection_fraction",
            "is_primary_district",
        ],
    ].reset_index(drop=True)
    missing = set(scenes["scene_footprint_id"]) - set(
        result["scene_footprint_id"]
    )
    if missing:
        raise SceneFootprintError(
            f"{len(missing)} scenes have no positive-area district mapping"
        )
    return result
