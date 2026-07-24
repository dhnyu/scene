"""Split coverage and deterministic overlap-multiplicity statistics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from scene.core.config import ProjectConfig
from scene.scenes.allowable_region import SPLITS
from scene.scenes.models import SceneGenerationResult


def scene_statistics(
    result: SceneGenerationResult,
    config: ProjectConfig,
) -> dict[str, Any]:
    scene_config = config.scene_generation
    if scene_config is None:
        raise ValueError("scene_generation configuration is required")
    cell_area = scene_config.stride_x_m * scene_config.stride_y_m
    scene_area = scene_config.scene_width_m * scene_config.scene_height_m
    split_rows: dict[str, dict[str, Any]] = {}
    multiplicity: dict[str, dict[str, int]] = {}
    district_counts = {
        str(item["split"]): 0
        for item in result.assignment_lock["assignment"]
    }
    for item in result.assignment_lock["assignment"]:
        split = str(item["split"])
        district_counts[split] = district_counts.get(split, 0) + 1
    for split in SPLITS:
        frame = result.scenes.loc[result.scenes["split"] == split]
        coverage: Counter[tuple[int, int]] = Counter()
        for row in frame.itertuples():
            for col_offset in (-1, 0):
                for row_offset in (-1, 0):
                    coverage[
                        (
                            int(row.grid_col) + col_offset,
                            int(row.grid_row) + row_offset,
                        )
                    ] += 1
        histogram = Counter(coverage.values())
        union_area = len(coverage) * cell_area
        allowable_area = float(
            result.allowable_regions.allowable[split].area
        )
        raw_area = float(result.allowable_regions.raw_unions[split].area)
        scene_count = len(frame)
        multiplicity[split] = {
            str(level): int(count)
            for level, count in sorted(histogram.items())
        }
        split_rows[split] = {
            "district_count": district_counts[split],
            "raw_split_union_area_m2": raw_area,
            "allowable_area_m2": allowable_area,
            "exclusion_area_m2": raw_area - allowable_area,
            "candidate_footprint_count": (
                result.eligibility.candidate_count_by_split[split]
            ),
            "eligible_footprint_count": scene_count,
            "rejected_footprint_count": (
                result.eligibility.candidate_count_by_split[split]
                - scene_count
            ),
            "final_scene_count": scene_count,
            "footprint_area_sum_m2": scene_count * scene_area,
            "footprint_union_area_m2": union_area,
            "coverage_ratio": union_area / allowable_area,
            "allowable_gap_area_m2": allowable_area - union_area,
            "outside_allowable_area_m2": 0.0,
            "average_coverage_multiplicity": (
                sum(level * count for level, count in histogram.items())
                / len(coverage)
            ),
            "maximum_coverage_multiplicity": max(coverage.values()),
            "unique_intersecting_districts": int(
                result.district_mapping.loc[
                    result.district_mapping["split"] == split,
                    "district_id",
                ].nunique()
            ),
        }
    return {
        "candidate_scene_count": result.eligibility.candidate_count,
        "rejected_scene_count": result.eligibility.rejected_count,
        "rejection_reasons": result.eligibility.rejection_reasons,
        "split": split_rows,
        "coverage_multiplicity_cell_counts": multiplicity,
    }
