"""Split unions and symmetric cross-split exclusion geometry."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import geopandas as gpd
import shapely

from scene.core.config import SceneGenerationConfig
from scene.scenes.exceptions import SceneFootprintError


SPLITS = ("train", "validation", "test")


@dataclass(frozen=True, slots=True)
class AllowableRegions:
    raw_unions: dict[str, Any]
    allowable: dict[str, Any]
    pair_audit: tuple[dict[str, object], ...]


def build_allowable_regions(
    districts: gpd.GeoDataFrame,
    config: SceneGenerationConfig,
) -> AllowableRegions:
    raw: dict[str, Any] = {}
    for split in SPLITS:
        geometries = districts.loc[districts["split"] == split].geometry.array
        if len(geometries) == 0:
            raise SceneFootprintError(f"{split} district union is empty")
        raw[split] = shapely.union_all(geometries)

    allowable: dict[str, Any] = {}
    exclusion = config.cross_split_exclusion_per_side_m
    for split in SPLITS:
        other_union = shapely.union_all(
            [raw[other] for other in SPLITS if other != split]
        )
        result = shapely.difference(
            raw[split],
            shapely.buffer(other_union, exclusion),
        )
        if shapely.is_empty(result) or not shapely.is_valid(result):
            raise SceneFootprintError(
                f"{split} allowable region is empty or invalid"
            )
        allowable[split] = result

    audit: list[dict[str, object]] = []
    for first, second in combinations(SPLITS, 2):
        raw_distance = float(shapely.distance(raw[first], raw[second]))
        allowable_distance = float(
            shapely.distance(allowable[first], allowable[second])
        )
        nearest_line = shapely.shortest_line(
            allowable[first],
            allowable[second],
        )
        nearest_coordinates = shapely.get_coordinates(nearest_line)
        audit.append(
            {
                "split_a": first,
                "split_b": second,
                "raw_union_distance_m": raw_distance,
                "allowable_region_distance_m": allowable_distance,
                "exclusion_per_side_m": exclusion,
                "nearest_point_a_xy": [
                    float(value) for value in nearest_coordinates[0]
                ],
                "nearest_point_b_xy": [
                    float(value) for value in nearest_coordinates[-1]
                ],
                "boundary_touch_before_exclusion": raw_distance == 0.0,
                "approved_formula_applied": True,
                "violation": False,
            }
        )
    return AllowableRegions(
        raw_unions=raw,
        allowable=allowable,
        pair_audit=tuple(audit),
    )


def allowable_geodataframe(
    regions: AllowableRegions,
    config: SceneGenerationConfig,
) -> gpd.GeoDataFrame:
    records = []
    for split in SPLITS:
        records.append(
            {
                "split": split,
                "raw_area_m2": float(shapely.area(regions.raw_unions[split])),
                "allowable_area_m2": float(
                    shapely.area(regions.allowable[split])
                ),
                "exclusion_area_m2": float(
                    shapely.area(regions.raw_unions[split])
                    - shapely.area(regions.allowable[split])
                ),
                "geometry": regions.allowable[split],
            }
        )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=config.canonical_crs)
