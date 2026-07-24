"""Fatal M1.7 scene, leakage, mapping, and determinism validation."""

from __future__ import annotations

from itertools import combinations
from typing import Any

import geopandas as gpd
import numpy as np
import shapely

from scene.core.config import ProjectConfig
from scene.scenes.allowable_region import SPLITS
from scene.scenes.exceptions import SceneFootprintError
from scene.scenes.models import SceneGenerationResult


def _cross_split_scene_intersections(
    scenes: gpd.GeoDataFrame,
) -> int:
    total = 0
    for first, second in combinations(SPLITS, 2):
        left = scenes.loc[
            scenes["split"] == first,
            ["scene_footprint_id", "geometry"],
        ].rename(columns={"scene_footprint_id": "left_scene_id"})
        right = scenes.loc[
            scenes["split"] == second,
            ["scene_footprint_id", "geometry"],
        ].rename(columns={"scene_footprint_id": "right_scene_id"})
        candidates = gpd.sjoin(
            left,
            right,
            predicate="intersects",
            how="inner",
        )
        if candidates.empty:
            continue
        right_geometries = gpd.GeoSeries(
            right.geometry.loc[
                candidates["index_right"].to_numpy()
            ].array,
            index=candidates.index,
            crs=scenes.crs,
        )
        areas = candidates.geometry.intersection(
            right_geometries,
            align=False,
        ).area
        total += int((areas > 0.0).sum())
    return total


def _other_split_raw_intersections(
    scenes: gpd.GeoDataFrame,
    result: SceneGenerationResult,
) -> int:
    total = 0
    for split in SPLITS:
        geometries = scenes.loc[scenes["split"] == split].geometry.array
        for other in SPLITS:
            if other == split:
                continue
            areas = shapely.area(
                shapely.intersection(
                    geometries,
                    result.allowable_regions.raw_unions[other],
                )
            )
            total += int(np.count_nonzero(areas > 0.0))
    return total


def _other_split_mapping_count(
    mapping: Any,
    districts: gpd.GeoDataFrame,
) -> tuple[int, int]:
    district_splits = districts[
        ["district_id", "split"]
    ].rename(columns={"split": "district_split"})
    checked = mapping.merge(
        district_splits,
        on="district_id",
        how="left",
        validate="many_to_one",
    )
    unknown = int(checked["district_split"].isna().sum())
    mismatched = int(
        (
            checked["district_split"].notna()
            & (checked["split"] != checked["district_split"])
        ).sum()
    )
    return unknown, mismatched


def validate_scene_result(
    result: SceneGenerationResult,
    config: ProjectConfig,
    *,
    regenerated: SceneGenerationResult | None = None,
) -> dict[str, Any]:
    scene_config = config.scene_generation
    if scene_config is None:
        raise SceneFootprintError("scene_generation configuration is required")
    scenes = result.scenes
    mapping = result.district_mapping
    width = scenes.geometry.bounds["maxx"] - scenes.geometry.bounds["minx"]
    height = scenes.geometry.bounds["maxy"] - scenes.geometry.bounds["miny"]
    area = scenes.geometry.area
    expected_area = scene_config.scene_width_m * scene_config.scene_height_m
    coordinate_counts = [
        len(shapely.get_coordinates(geometry))
        for geometry in scenes.geometry.array
    ]
    geometry_metrics = {
        "scene_count": len(scenes),
        "null_id_count": int(scenes["scene_footprint_id"].isna().sum()),
        "duplicate_id_count": int(
            scenes["scene_footprint_id"].duplicated().sum()
        ),
        "duplicate_grid_index_count": int(
            scenes.duplicated(["grid_col", "grid_row"]).sum()
        ),
        "null_geometry_count": int(scenes.geometry.isna().sum()),
        "empty_geometry_count": int(scenes.geometry.is_empty.sum()),
        "invalid_geometry_count": int((~scenes.geometry.is_valid).sum()),
        "width_error_count": int(
            np.count_nonzero(
                np.abs(width - scene_config.scene_width_m)
                > scene_config.linear_tolerance_m
            )
        ),
        "height_error_count": int(
            np.count_nonzero(
                np.abs(height - scene_config.scene_height_m)
                > scene_config.linear_tolerance_m
            )
        ),
        "area_error_count": int(
            np.count_nonzero(
                np.abs(area - expected_area)
                > scene_config.area_tolerance_m2
            )
        ),
        "non_axis_aligned_count": int(
            sum(count != 5 for count in coordinate_counts)
        ),
        "crs_error_count": int(
            scenes.crs is None or scenes.crs.to_epsg() != 5186
        ),
    }
    counts = scenes["split"].value_counts().to_dict()
    cover_count = np.column_stack(
        [
            np.asarray(
                shapely.covers(
                    result.allowable_regions.allowable[split],
                    scenes.geometry.array,
                )
            )
            for split in SPLITS
        ]
    ).sum(axis=1)
    split_metrics = {
        "scene_count_by_split": {
            split: int(counts.get(split, 0)) for split in SPLITS
        },
        "unassigned_scene_count": int(scenes["split"].isna().sum()),
        "multi_split_scene_count": int(np.count_nonzero(cover_count > 1)),
        "scene_not_covered_by_exactly_one_split_count": int(
            np.count_nonzero(cover_count != 1)
        ),
        "cross_split_positive_area_intersection_count": (
            _cross_split_scene_intersections(scenes)
        ),
        "other_split_raw_region_intersection_count": (
            _other_split_raw_intersections(scenes, result)
        ),
        "allowable_region_pair_distance_violation_count": int(
            sum(bool(row["violation"]) for row in result.allowable_regions.pair_audit)
        ),
    }
    primary_counts = mapping.groupby("scene_footprint_id")[
        "is_primary_district"
    ].sum()
    mapped_ids = set(mapping["scene_footprint_id"])
    scene_ids = set(scenes["scene_footprint_id"])
    unknown_districts, other_split_mappings = (
        _other_split_mapping_count(mapping, result.districts)
    )
    mapping_metrics = {
        "scene_without_mapping_count": len(scene_ids - mapped_ids),
        "scene_without_primary_count": int((primary_counts == 0).sum()),
        "scene_with_multiple_primary_count": int((primary_counts > 1).sum()),
        "unknown_scene_id_count": len(mapped_ids - scene_ids),
        "unknown_district_id_count": unknown_districts,
        "other_split_district_mapping_count": other_split_mappings,
        "negative_intersection_area_count": int(
            (mapping["intersection_area_m2"] < 0).sum()
        ),
        "negative_intersection_fraction_count": int(
            (mapping["intersection_fraction"] < 0).sum()
        ),
    }
    sums = mapping.groupby("scene_footprint_id")["intersection_area_m2"].sum()
    mapping_metrics["intersection_area_sum_error_count"] = int(
        np.count_nonzero(
            np.abs(sums.reindex(scenes["scene_footprint_id"]).to_numpy() - expected_area)
            > scene_config.area_tolerance_m2
        )
    )

    deterministic = {
        "checked": regenerated is not None,
        "scene_count_equal": False,
        "scene_id_set_equal": False,
        "grid_index_set_equal": False,
        "split_assignment_equal": False,
        "geometry_fingerprint_equal": False,
        "district_mapping_equal": False,
        "primary_district_equal": False,
        "content_hash_equal": False,
    }
    if regenerated is not None:
        deterministic.update(
            {
                "scene_count_equal": len(scenes) == len(regenerated.scenes),
                "scene_id_set_equal": scene_ids
                == set(regenerated.scenes["scene_footprint_id"]),
                "grid_index_set_equal": set(
                    zip(scenes.grid_col, scenes.grid_row, strict=True)
                )
                == set(
                    zip(
                        regenerated.scenes.grid_col,
                        regenerated.scenes.grid_row,
                        strict=True,
                    )
                ),
                "split_assignment_equal": dict(
                    zip(
                        scenes.scene_footprint_id,
                        scenes.split,
                        strict=True,
                    )
                )
                == dict(
                    zip(
                        regenerated.scenes.scene_footprint_id,
                        regenerated.scenes.split,
                        strict=True,
                    )
                ),
                "geometry_fingerprint_equal": [
                    tuple(geometry.bounds)
                    for geometry in scenes.sort_values(
                        "scene_footprint_id"
                    ).geometry
                ]
                == [
                    tuple(geometry.bounds)
                    for geometry in regenerated.scenes.sort_values(
                        "scene_footprint_id"
                    ).geometry
                ],
                "district_mapping_equal": result.district_mapping.equals(
                    regenerated.district_mapping
                ),
                "primary_district_equal": mapping.loc[
                    mapping.is_primary_district,
                    ["scene_footprint_id", "district_id"],
                ].reset_index(drop=True).equals(
                    regenerated.district_mapping.loc[
                        regenerated.district_mapping.is_primary_district,
                        ["scene_footprint_id", "district_id"],
                    ].reset_index(drop=True)
                ),
                "content_hash_equal": result.content_hash
                == regenerated.content_hash,
            }
        )
    all_deterministic = deterministic["checked"] and all(
        value for key, value in deterministic.items() if key != "checked"
    )
    fatal_counts = {
        **{
            key: value
            for key, value in geometry_metrics.items()
            if key != "scene_count"
        },
        **{
            key: value
            for key, value in split_metrics.items()
            if key != "scene_count_by_split"
        },
        **mapping_metrics,
    }
    valid = (
        geometry_metrics["scene_count"] > 0
        and all(counts.get(split, 0) > 0 for split in SPLITS)
        and all(value == 0 for value in fatal_counts.values())
        and all_deterministic
    )
    report = {
        "determinism": {**deterministic, "valid": all_deterministic},
        "geometry": geometry_metrics,
        "mapping": mapping_metrics,
        "split_and_leakage": split_metrics,
        "valid": valid,
    }
    if not valid:
        raise SceneFootprintError(f"scene validation failed: {report}")
    return report
