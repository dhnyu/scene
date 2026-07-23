from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import Polygon

from scene.core.config import DistrictAssignmentConfig
from scene.split.assign import build_assignment
from scene.split.balancing import prepare_balance_model, search_assignment
from scene.split.provenance import (
    BalancingStatistics,
    CanonicalDistrictInput,
)
from scene.split.validator import validate_assignment


def make_split_config(tmp_path: Path) -> DistrictAssignmentConfig:
    return DistrictAssignmentConfig(
        assignment_version="test-v1",
        assignment_seed=20260723,
        train_count=15,
        validation_count=5,
        test_count=5,
        canonical_boundary_path=tmp_path / "districts.gpkg",
        canonical_boundary_layer="seoul_sigungu",
        canonical_boundary_content_hash="a" * 64,
        building_geometry_path=tmp_path / "buildings.gpkg",
        building_geometry_layer="buildings",
        road_geometry_path=tmp_path / "roads.gpkg",
        road_geometry_layer="road_links",
        poi_geometry_path=tmp_path / "pois.gpkg",
        poi_geometry_layer="pois",
        poi_attributes_path=tmp_path / "pois.parquet",
        landcover_source_name="landcover",
        dem_source_name="dem",
        epsg=5186,
        scene_side_length_m=500.0,
        scene_stride_m=250.0,
        grid_origin_m=(0.0, 0.0),
        cross_split_buffer_m=250.0,
        optimizer_algorithm_version="test-v1",
        candidate_count=10_000,
        spatial_cluster_count=5,
        context_cluster_count=5,
        validation_test_min_context_clusters=3,
        validation_test_component_min=2,
        validation_test_component_max=3,
        objective_weights={
            "category_distribution": 0.75,
            "context_diversity": 0.5,
            "dem_distribution": 0.5,
            "density_balance": 0.75,
            "extensive_balance": 1.0,
            "landcover_distribution": 1.0,
            "spatial_diversity": 0.5,
        },
    )


def make_split_fixture(
    tmp_path: Path,
) -> tuple[
    DistrictAssignmentConfig,
    CanonicalDistrictInput,
    BalancingStatistics,
]:
    config = make_split_config(tmp_path)
    geometry = []
    rows = []
    for index in range(25):
        x = index % 5
        y = index // 5
        code = f"11{index:03d}"
        geometry.append(
            Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])
        )
        rows.append(
            {
                "district_id": f"{index:064x}",
                "district_code": code,
                "district_name": f"district-{index}",
                "area_m2": 1_000_000.0 + index * 1000,
                "area_km2": 1.0 + index / 1000,
                "eligible_scene_estimate": 100 + index,
                "building_count": 1000 + index * 20,
                "building_density_per_km2": 1000 + index * 10,
                "road_length_m": 10000.0 + index * 100,
                "road_length_km": 10.0 + index / 10,
                "road_density_km_per_km2": 10.0 + index / 20,
                "poi_count": 500 + index * 10,
                "poi_density_per_km2": 500 + index * 5,
                "poi_category_1_counts": {
                    "a": 100 + index,
                    "b": 80 + (index % 4),
                    "c": 40 + (index % 3),
                },
                "landcover_cell_count": 1000,
                "landcover_nodata_count": 0,
                "landcover_raw_code_counts": {
                    "1": 300 + index,
                    "2": 400 - index,
                    "3": 300,
                },
                "dem_valid_cell_count": 100,
                "dem_nodata_count": 0,
                "dem_min_raw": 1.0,
                "dem_q25_raw": 10.0 + index,
                "dem_q50_raw": 20.0 + index,
                "dem_q75_raw": 30.0 + index,
                "dem_max_raw": 100.0 + index,
                "dem_mean_raw": 25.0 + index,
                "dem_std_raw": 5.0 + (index % 4),
                "centroid_x_m": x + 0.5,
                "centroid_y_m": y + 0.5,
            }
        )
    districts = gpd.GeoDataFrame(
        {
            "district_id": [row["district_id"] for row in rows],
            "district_code": [row["district_code"] for row in rows],
            "district_name": [row["district_name"] for row in rows],
        },
        geometry=geometry,
        crs="EPSG:5186",
    )
    canonical = CanonicalDistrictInput(
        districts=districts,
        geopackage_path=config.canonical_boundary_path,
        layer=config.canonical_boundary_layer,
        geopackage_sha256="b" * 64,
        content_hash="a" * 64,
    )
    statistics = BalancingStatistics(
        frame=pd.DataFrame(rows),
        landcover_codes=("1", "2", "3"),
        poi_categories=("a", "b", "c"),
        source_provenance={},
        method={},
        statistics_hash="c" * 64,
    )
    return config, canonical, statistics


def test_assignment_is_deterministic_and_has_15_5_5(tmp_path: Path) -> None:
    config, canonical, statistics = make_split_fixture(tmp_path)
    model = prepare_balance_model(statistics, canonical.districts, config)
    first = search_assignment(statistics, model, config)
    second = search_assignment(statistics, model, config)
    assignment = build_assignment(
        canonical,
        model,
        first,
        config,
        run_id="20260724_100000_KST",
    )
    regenerated = build_assignment(
        canonical,
        model,
        second,
        config,
        run_id="20260724_100001_KST",
    )
    validation = validate_assignment(
        assignment,
        config,
        regenerated_assignment=regenerated,
    )

    assert first.assignment == second.assignment
    assert assignment.assignment_hash == regenerated.assignment_hash
    assert validation.valid
    assert (
        validation.train_count,
        validation.validation_count,
        validation.test_count,
    ) == (15, 5, 5)
    assert validation.duplicate_district_count == 0
    assert validation.unassigned_district_count == 0
    assert first.feasible_candidate_count > 0
