"""Exact full-footprint scene eligibility."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import shapely

from scene.scenes.allowable_region import AllowableRegions, SPLITS
from scene.scenes.exceptions import SceneFootprintError


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: gpd.GeoDataFrame
    candidate_count: int
    rejected_count: int
    rejection_reasons: dict[str, int]
    candidate_count_by_split: dict[str, int]


def select_eligible_scenes(
    candidates: gpd.GeoDataFrame,
    regions: AllowableRegions,
) -> EligibilityResult:
    geometries = candidates.geometry.array
    covered = np.column_stack(
        [
            np.asarray(shapely.covers(regions.allowable[split], geometries))
            for split in SPLITS
        ]
    )
    cover_count = covered.sum(axis=1)
    multi_count = int(np.count_nonzero(cover_count > 1))
    if multi_count:
        raise SceneFootprintError(
            f"{multi_count} candidates are covered by multiple splits"
        )
    eligible_mask = cover_count == 1
    eligible = candidates.loc[eligible_mask].copy()
    split_index = np.argmax(covered[eligible_mask], axis=1)
    eligible["split"] = [SPLITS[index] for index in split_index]
    eligible.sort_values(["grid_row", "grid_col"], inplace=True)
    eligible.reset_index(drop=True, inplace=True)

    candidate_count_by_split = {
        split: int(
            np.count_nonzero(
                shapely.intersects(regions.raw_unions[split], geometries)
            )
        )
        for split in SPLITS
    }
    rejected = int(np.count_nonzero(~eligible_mask))
    return EligibilityResult(
        eligible=eligible,
        candidate_count=len(candidates),
        rejected_count=rejected,
        rejection_reasons={"outside_all_allowable_regions": rejected},
        candidate_count_by_split=candidate_count_by_split,
    )
