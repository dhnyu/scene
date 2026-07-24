"""Immutable M1.7 in-memory result containers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd

from scene.scenes.allowable_region import AllowableRegions
from scene.scenes.eligibility import EligibilityResult


@dataclass(frozen=True, slots=True)
class SceneGenerationResult:
    scenes: gpd.GeoDataFrame
    districts: gpd.GeoDataFrame
    district_mapping: pd.DataFrame
    allowable_regions: AllowableRegions
    allowable_frame: gpd.GeoDataFrame
    eligibility: EligibilityResult
    assignment_lock: dict[str, Any]
    scene_generation_config_hash: str
    content_hash: str
