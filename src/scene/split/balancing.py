"""Deterministic constrained search for the fixed 15/5/5 district split."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import random
from statistics import median
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

from scene.core.config import DistrictAssignmentConfig
from scene.split.exceptions import DistrictAssignmentError
from scene.split.provenance import AssignmentSearch, BalancingStatistics


SPLITS = ("train", "validation", "test")


@dataclass(frozen=True, slots=True)
class BalanceModel:
    frame: pd.DataFrame
    adjacency: dict[str, tuple[str, ...]]
    context_by_code: dict[str, int]
    spatial_by_code: dict[str, int]
    radial_by_code: dict[str, int]
    statistics_hash: str
    context_definition: dict[str, object]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _standardize(values: np.ndarray) -> np.ndarray:
    mean = values.mean(axis=0)
    standard_deviation = values.std(axis=0)
    standard_deviation[standard_deviation == 0.0] = 1.0
    return (values - mean) / standard_deviation


def _canonical_kmeans(
    features: np.ndarray,
    labels: list[str],
    cluster_count: int,
    seed: int,
) -> np.ndarray:
    """Lloyd clustering with deterministic farthest-point initialization."""

    values = _standardize(features.astype(np.float64))
    first = min(
        range(len(labels)),
        key=lambda index: hashlib.sha256(
            f"{seed}:{labels[index]}".encode("utf-8")
        ).hexdigest(),
    )
    centroid_indices = [first]
    while len(centroid_indices) < cluster_count:
        distances = np.min(
            np.stack(
                [
                    np.sum((values - values[index]) ** 2, axis=1)
                    for index in centroid_indices
                ]
            ),
            axis=0,
        )
        for index in centroid_indices:
            distances[index] = -1.0
        maximum = float(np.max(distances))
        candidates = [
            index
            for index, distance in enumerate(distances)
            if math.isclose(distance, maximum, rel_tol=0.0, abs_tol=1e-15)
        ]
        centroid_indices.append(min(candidates, key=lambda index: labels[index]))

    centroids = values[centroid_indices].copy()
    assignments = np.zeros(len(values), dtype=np.int64)
    for _ in range(100):
        distances = np.stack(
            [np.sum((values - centroid) ** 2, axis=1) for centroid in centroids],
            axis=1,
        )
        new_assignments = np.argmin(distances, axis=1)
        if np.array_equal(new_assignments, assignments):
            assignments = new_assignments
            break
        assignments = new_assignments
        for cluster in range(cluster_count):
            members = values[assignments == cluster]
            if len(members) == 0:
                raise DistrictAssignmentError(
                    "deterministic context clustering produced an empty cluster"
                )
            centroids[cluster] = members.mean(axis=0)

    order = sorted(
        range(cluster_count),
        key=lambda cluster: tuple(float(value) for value in centroids[cluster]),
    )
    remap = {old: new for new, old in enumerate(order)}
    return np.array([remap[int(value)] for value in assignments], dtype=np.int64)


def _entropy(counts: dict[str, int]) -> float:
    values = np.array(list(counts.values()), dtype=np.float64)
    if values.size == 0 or values.sum() == 0.0:
        return 0.0
    probabilities = values / values.sum()
    return float(-np.sum(probabilities * np.log(probabilities)))


def _adjacency(
    districts: gpd.GeoDataFrame,
) -> dict[str, tuple[str, ...]]:
    neighbours: dict[str, set[str]] = {
        str(code): set() for code in districts["district_code"]
    }
    for left in range(len(districts)):
        left_code = str(districts.iloc[left]["district_code"])
        for right in range(left + 1, len(districts)):
            right_code = str(districts.iloc[right]["district_code"])
            if districts.geometry.iloc[left].intersects(
                districts.geometry.iloc[right]
            ):
                neighbours[left_code].add(right_code)
                neighbours[right_code].add(left_code)
    return {
        code: tuple(sorted(values))
        for code, values in sorted(neighbours.items())
    }


def _component_count(
    district_codes: Iterable[str],
    adjacency: dict[str, tuple[str, ...]],
) -> int:
    remaining = set(district_codes)
    components = 0
    while remaining:
        components += 1
        stack = [min(remaining)]
        remaining.remove(stack[0])
        while stack:
            code = stack.pop()
            for neighbour in adjacency[code]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
    return components


def prepare_balance_model(
    statistics: BalancingStatistics,
    districts: gpd.GeoDataFrame,
    config: DistrictAssignmentConfig,
) -> BalanceModel:
    """Add reportable numeric urban-context and spatial diagnostics."""

    frame = statistics.frame.copy()
    frame["landcover_entropy"] = frame[
        "landcover_raw_code_counts"
    ].map(_entropy)
    context_columns = [
        "building_density_per_km2",
        "road_density_km_per_km2",
        "poi_density_per_km2",
        "landcover_entropy",
        "dem_mean_raw",
        "dem_std_raw",
    ]
    context_features = frame[context_columns].to_numpy(dtype=np.float64)
    context_features[:, :3] = np.log1p(context_features[:, :3])
    codes = frame["district_code"].astype(str).to_list()
    frame["context_cluster_id"] = _canonical_kmeans(
        context_features,
        codes,
        config.context_cluster_count,
        config.assignment_seed,
    )
    spatial_features = frame[["centroid_x_m", "centroid_y_m"]].to_numpy(
        dtype=np.float64
    )
    frame["spatial_cluster_id"] = _canonical_kmeans(
        spatial_features,
        codes,
        config.spatial_cluster_count,
        config.assignment_seed + 1,
    )
    city_center = spatial_features.mean(axis=0)
    radial_distance = np.sqrt(
        np.sum((spatial_features - city_center) ** 2, axis=1)
    )
    frame["centroid_radial_distance_m"] = radial_distance
    radial_order = sorted(
        range(len(frame)),
        key=lambda index: (radial_distance[index], codes[index]),
    )
    radial_bands = np.zeros(len(frame), dtype=np.int64)
    for band, indices in enumerate(np.array_split(radial_order, 3)):
        radial_bands[np.asarray(indices, dtype=np.int64)] = band
    frame["radial_band_id"] = radial_bands
    adjacency = _adjacency(districts)
    records = json.loads(
        frame.to_json(orient="records", force_ascii=False, double_precision=15)
    )
    context_definition = {
        "context_cluster_algorithm": "deterministic-farthest-init-lloyd-v1",
        "context_cluster_columns": context_columns,
        "context_cluster_count": config.context_cluster_count,
        "spatial_cluster_columns": ["centroid_x_m", "centroid_y_m"],
        "spatial_cluster_count": config.spatial_cluster_count,
        "radial_band_definition": (
            "three equal-count bands of centroid distance from the arithmetic "
            "mean of all district centroids; no semantic urban label assigned"
        ),
    }
    statistics_hash = _canonical_hash(
        {
            "context_definition": context_definition,
            "districts": records,
            "raw_statistics_hash": statistics.statistics_hash,
        }
    )
    return BalanceModel(
        frame=frame,
        adjacency=adjacency,
        context_by_code={
            str(row.district_code): int(row.context_cluster_id)
            for row in frame.itertuples()
        },
        spatial_by_code={
            str(row.district_code): int(row.spatial_cluster_id)
            for row in frame.itertuples()
        },
        radial_by_code={
            str(row.district_code): int(row.radial_band_id)
            for row in frame.itertuples()
        },
        statistics_hash=statistics_hash,
        context_definition=context_definition,
    )


class AssignmentObjective:
    """Evaluate auditable balance components for one candidate assignment."""

    def __init__(
        self,
        model: BalanceModel,
        statistics: BalancingStatistics,
        config: DistrictAssignmentConfig,
    ) -> None:
        self.frame = model.frame
        self.statistics = statistics
        self.config = config
        self.index_by_code = {
            str(code): index
            for index, code in enumerate(self.frame["district_code"])
        }
        self.extensive_columns = (
            "area_km2",
            "eligible_scene_estimate",
            "building_count",
            "road_length_km",
            "poi_count",
        )
        self.density_columns = (
            "building_density_per_km2",
            "road_density_km_per_km2",
            "poi_density_per_km2",
        )
        self.extensive = self.frame[list(self.extensive_columns)].to_numpy(
            dtype=np.float64
        )
        self.density = self.frame[list(self.density_columns)].to_numpy(
            dtype=np.float64
        )
        self.landcover = np.array(
            [
                [
                    counts.get(code, 0)
                    for code in statistics.landcover_codes
                ]
                for counts in self.frame["landcover_raw_code_counts"]
            ],
            dtype=np.float64,
        )
        self.categories = np.array(
            [
                [
                    counts.get(category, 0)
                    for category in statistics.poi_categories
                ]
                for counts in self.frame["poi_category_1_counts"]
            ],
            dtype=np.float64,
        )
        self.dem_count = self.frame["dem_valid_cell_count"].to_numpy(
            dtype=np.float64
        )
        self.dem_mean = self.frame["dem_mean_raw"].to_numpy(dtype=np.float64)
        self.dem_std = self.frame["dem_std_raw"].to_numpy(dtype=np.float64)
        self.context = self.frame["context_cluster_id"].to_numpy(dtype=np.int64)
        self.spatial = self.frame["spatial_cluster_id"].to_numpy(dtype=np.int64)
        self.centroids = _standardize(
            self.frame[["centroid_x_m", "centroid_y_m"]].to_numpy(
                dtype=np.float64
            )
        )
        total_dem = self.dem_count.sum()
        self.city_dem_mean = float(
            np.sum(self.dem_count * self.dem_mean) / total_dem
        )
        self.city_dem_second = float(
            np.sum(
                self.dem_count * (self.dem_std**2 + self.dem_mean**2)
            )
            / total_dem
        )
        self.city_dem_std = math.sqrt(
            max(0.0, self.city_dem_second - self.city_dem_mean**2)
        )

    @staticmethod
    def _distribution(values: np.ndarray) -> np.ndarray:
        total = values.sum()
        return values / total if total > 0.0 else np.zeros_like(values)

    def score(
        self,
        assignment: dict[str, str],
    ) -> dict[str, float]:
        split_indices = {
            split: np.array(
                [
                    self.index_by_code[code]
                    for code, value in assignment.items()
                    if value == split
                ],
                dtype=np.int64,
            )
            for split in SPLITS
        }
        target = {
            "train": self.config.train_count / 25.0,
            "validation": self.config.validation_count / 25.0,
            "test": self.config.test_count / 25.0,
        }
        extensive_total = self.extensive.sum(axis=0)
        extensive_error: list[float] = []
        density_error: list[float] = []
        category_error: list[float] = []
        landcover_error: list[float] = []
        dem_error: list[float] = []
        context_error: list[float] = []
        spatial_error: list[float] = []
        city_density = np.average(
            self.density,
            axis=0,
            weights=self.extensive[:, 0],
        )
        city_category = self._distribution(self.categories.sum(axis=0))
        city_landcover = self._distribution(self.landcover.sum(axis=0))
        city_context = np.bincount(
            self.context,
            minlength=self.config.context_cluster_count,
        ) / 25.0
        city_spatial = np.bincount(
            self.spatial,
            minlength=self.config.spatial_cluster_count,
        ) / 25.0
        for split, indices in split_indices.items():
            share = self.extensive[indices].sum(axis=0) / extensive_total
            extensive_error.extend(
                ((share - target[split]) / target[split]) ** 2
            )
            split_density = np.average(
                self.density[indices],
                axis=0,
                weights=self.extensive[indices, 0],
            )
            density_error.extend(
                ((split_density - city_density) / np.maximum(city_density, 1e-9))
                ** 2
            )
            split_category = self._distribution(
                self.categories[indices].sum(axis=0)
            )
            category_error.append(
                float(np.sum(np.abs(split_category - city_category)) / 2.0)
                ** 2
            )
            split_landcover = self._distribution(
                self.landcover[indices].sum(axis=0)
            )
            landcover_error.append(
                float(np.sum(np.abs(split_landcover - city_landcover)) / 2.0)
                ** 2
            )
            weights = self.dem_count[indices]
            dem_mean = float(
                np.sum(weights * self.dem_mean[indices]) / weights.sum()
            )
            dem_second = float(
                np.sum(
                    weights
                    * (
                        self.dem_std[indices] ** 2
                        + self.dem_mean[indices] ** 2
                    )
                )
                / weights.sum()
            )
            dem_std = math.sqrt(max(0.0, dem_second - dem_mean**2))
            dem_error.extend(
                [
                    ((dem_mean - self.city_dem_mean) / self.city_dem_std) ** 2,
                    ((dem_std - self.city_dem_std) / self.city_dem_std) ** 2,
                ]
            )
            context_distribution = np.bincount(
                self.context[indices],
                minlength=self.config.context_cluster_count,
            ) / len(indices)
            context_error.append(
                float(
                    np.mean((context_distribution - city_context) ** 2)
                )
            )
            spatial_distribution = np.bincount(
                self.spatial[indices],
                minlength=self.config.spatial_cluster_count,
            ) / len(indices)
            spatial_error.extend(
                [
                    float(
                        np.mean((spatial_distribution - city_spatial) ** 2)
                    ),
                    float(np.mean(self.centroids[indices], axis=0) @ np.mean(
                        self.centroids[indices], axis=0
                    )),
                ]
            )
        return {
            "category_distribution": float(np.mean(category_error)),
            "context_diversity": float(np.mean(context_error)),
            "dem_distribution": float(np.mean(dem_error)),
            "density_balance": float(np.mean(density_error)),
            "extensive_balance": float(np.mean(extensive_error)),
            "landcover_distribution": float(np.mean(landcover_error)),
            "spatial_diversity": float(np.mean(spatial_error)),
        }


def _diagnostics(
    assignment: dict[str, str],
    model: BalanceModel,
) -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    context: dict[str, int] = {}
    spatial: dict[str, int] = {}
    components: dict[str, int] = {}
    radial: dict[str, int] = {}
    for split in SPLITS:
        codes = sorted(code for code, value in assignment.items() if value == split)
        context[split] = len(
            {model.context_by_code[code] for code in codes}
        )
        spatial[split] = len(
            {model.spatial_by_code[code] for code in codes}
        )
        components[split] = _component_count(codes, model.adjacency)
        radial[split] = len(
            {model.radial_by_code[code] for code in codes}
        )
    return context, spatial, components, radial


def _feasible(
    assignment: dict[str, str],
    model: BalanceModel,
    config: DistrictAssignmentConfig,
) -> tuple[bool, tuple[dict[str, int], ...]]:
    diagnostics = _diagnostics(assignment, model)
    context, spatial, components, radial = diagnostics
    valid = all(
        context[split] >= config.validation_test_min_context_clusters
        and spatial[split] >= 2
        and radial[split] >= 2
        and config.validation_test_component_min
        <= components[split]
        <= config.validation_test_component_max
        for split in ("validation", "test")
    ) and context["train"] >= config.validation_test_min_context_clusters
    return valid, diagnostics


def search_assignment(
    statistics: BalancingStatistics,
    model: BalanceModel,
    config: DistrictAssignmentConfig,
) -> AssignmentSearch:
    """Select the best feasible candidate from a seeded reproducible search."""

    objective = AssignmentObjective(model, statistics, config)
    codes = sorted(model.frame["district_code"].astype(str))
    random_generator = random.Random(config.assignment_seed)
    best_assignment: dict[str, str] | None = None
    best_components: dict[str, float] | None = None
    best_score = math.inf
    best_tie = ""
    best_diagnostics: tuple[dict[str, int], ...] | None = None
    feasible_scores: list[float] = []
    weights = config.objective_weights
    for _ in range(config.candidate_count):
        candidate_codes = codes.copy()
        random_generator.shuffle(candidate_codes)
        assignment = {
            code: (
                "train"
                if index < config.train_count
                else "validation"
                if index < config.train_count + config.validation_count
                else "test"
            )
            for index, code in enumerate(candidate_codes)
        }
        feasible, diagnostics = _feasible(assignment, model, config)
        if not feasible:
            continue
        components = objective.score(assignment)
        score = sum(weights[key] * value for key, value in components.items())
        feasible_scores.append(score)
        tie = "|".join(f"{code}:{assignment[code]}" for code in codes)
        if score < best_score or (
            math.isclose(score, best_score, rel_tol=0.0, abs_tol=1e-15)
            and tie < best_tie
        ):
            best_assignment = assignment
            best_components = components
            best_score = score
            best_tie = tie
            best_diagnostics = diagnostics
    if (
        best_assignment is None
        or best_components is None
        or best_diagnostics is None
    ):
        raise DistrictAssignmentError(
            "no feasible district assignment was found by the configured search"
        )
    context, spatial, components, radial = best_diagnostics
    return AssignmentSearch(
        assignment=dict(sorted(best_assignment.items())),
        score=best_score,
        component_scores=best_components,
        candidate_count=config.candidate_count,
        feasible_candidate_count=len(feasible_scores),
        feasible_score_median=float(median(feasible_scores)),
        context_cluster_count_by_split=context,
        spatial_cluster_count_by_split=spatial,
        connected_component_count_by_split=components,
        radial_band_count_by_split=radial,
    )
