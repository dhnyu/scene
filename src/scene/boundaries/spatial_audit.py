"""Spatial consistency checks without geometry repair or concealment."""

from __future__ import annotations

from itertools import combinations
from typing import Any

import geopandas as gpd
from shapely import union_all


def audit_spatial_consistency(
    districts: gpd.GeoDataFrame,
    seoul_boundary: gpd.GeoDataFrame,
) -> dict[str, Any]:
    """Return exact area metrics and distinguish touches from area overlaps."""

    if seoul_boundary.crs != districts.crs:
        seoul_boundary = seoul_boundary.to_crs(districts.crs)
    city = union_all(seoul_boundary.geometry.array)
    district_union = union_all(districts.geometry.array)
    overlaps: list[dict[str, object]] = []
    intersecting = 0
    for left_index, right_index in combinations(range(len(districts)), 2):
        left = districts.iloc[left_index]
        right = districts.iloc[right_index]
        if not left.geometry.intersects(right.geometry):
            continue
        intersecting += 1
        area = float(left.geometry.intersection(right.geometry).area)
        if area > 0.0:
            overlaps.append(
                {
                    "left_district_code": str(left.district_code),
                    "left_district_name": str(left.district_name),
                    "right_district_code": str(right.district_code),
                    "right_district_name": str(right.district_name),
                    "overlap_area_m2": area,
                }
            )
    overlap_areas = [float(item["overlap_area_m2"]) for item in overlaps]
    intersection_area = float(district_union.intersection(city).area)
    outside_area = float(district_union.difference(city).area)
    gap_area = float(city.difference(district_union).area)
    symmetric_difference = float(district_union.symmetric_difference(city).area)
    city_area = float(city.area)
    return {
        "seoul_boundary_area_m2": city_area,
        "district_area_sum_m2": float(districts.geometry.area.sum()),
        "district_union_area_m2": float(district_union.area),
        "district_union_intersection_area_m2": intersection_area,
        "intersecting_district_pair_count": intersecting,
        "positive_area_overlap_pair_count": len(overlaps),
        "pairwise_overlap_total_area_m2": sum(overlap_areas),
        "maximum_pairwise_overlap_area_m2": max(overlap_areas, default=0.0),
        "overlap_pairs": overlaps,
        "outside_area_m2": outside_area,
        "gap_area_m2": gap_area,
        "symmetric_difference_area_m2": symmetric_difference,
        "symmetric_difference_ratio": (
            symmetric_difference / city_area if city_area else None
        ),
    }
