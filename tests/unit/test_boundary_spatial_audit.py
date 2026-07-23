from __future__ import annotations

import geopandas as gpd
from shapely import Polygon

from scene.boundaries.spatial_audit import audit_spatial_consistency


def _frame(polygons: list[Polygon]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "district_code": [str(index) for index in range(len(polygons))],
            "district_name": [f"d{index}" for index in range(len(polygons))],
        },
        geometry=polygons,
        crs="EPSG:5186",
    )


def test_boundary_touch_is_not_positive_area_overlap() -> None:
    districts = _frame(
        [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ]
    )
    city = gpd.GeoDataFrame(
        geometry=[Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])],
        crs="EPSG:5186",
    )

    result = audit_spatial_consistency(districts, city)

    assert result["intersecting_district_pair_count"] == 1
    assert result["positive_area_overlap_pair_count"] == 0
    assert result["pairwise_overlap_total_area_m2"] == 0.0
    assert result["gap_area_m2"] == 0.0


def test_positive_overlap_gap_and_outside_are_reported() -> None:
    districts = _frame(
        [
            Polygon([(0, 0), (1.2, 0), (1.2, 1), (0, 1)]),
            Polygon([(1, 0), (2.2, 0), (2.2, 1), (1, 1)]),
        ]
    )
    city = gpd.GeoDataFrame(
        geometry=[Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])],
        crs="EPSG:5186",
    )

    result = audit_spatial_consistency(districts, city)

    assert result["positive_area_overlap_pair_count"] == 1
    assert result["pairwise_overlap_total_area_m2"] > 0
    assert result["outside_area_m2"] > 0
