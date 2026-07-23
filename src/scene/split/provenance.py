"""Typed M1.6 statistics, assignment, and provenance records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd


@dataclass(frozen=True, slots=True)
class CanonicalDistrictInput:
    districts: gpd.GeoDataFrame
    geopackage_path: Path
    layer: str
    geopackage_sha256: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class BalancingStatistics:
    frame: pd.DataFrame
    landcover_codes: tuple[str, ...]
    poi_categories: tuple[str, ...]
    source_provenance: dict[str, dict[str, object]]
    method: dict[str, object]
    statistics_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "districts": self.frame.to_dict(orient="records"),
            "landcover_codes": list(self.landcover_codes),
            "method": self.method,
            "poi_categories": list(self.poi_categories),
            "source_provenance": self.source_provenance,
            "statistics_hash": self.statistics_hash,
        }


@dataclass(frozen=True, slots=True)
class AssignmentSearch:
    assignment: dict[str, str]
    score: float
    component_scores: dict[str, float]
    candidate_count: int
    feasible_candidate_count: int
    feasible_score_median: float
    context_cluster_count_by_split: dict[str, int]
    spatial_cluster_count_by_split: dict[str, int]
    connected_component_count_by_split: dict[str, int]
    radial_band_count_by_split: dict[str, int]


@dataclass(frozen=True, slots=True)
class DistrictAssignment:
    frame: gpd.GeoDataFrame
    assignment_hash: str
    assignment_config_hash: str
    balance_statistics_hash: str
    search: AssignmentSearch
    canonical_input: CanonicalDistrictInput


@dataclass(frozen=True, slots=True)
class AssignmentValidation:
    district_count: int
    train_count: int
    validation_count: int
    test_count: int
    duplicate_district_count: int
    unassigned_district_count: int
    duplicate_split_assignment_count: int
    deterministic_regeneration: bool
    assignment_hash_deterministic: bool
    provenance_complete: bool
    provenance_missing_count: int
    canonical_crs_valid: bool
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
