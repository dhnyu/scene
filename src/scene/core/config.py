"""Typed project configuration loading and canonical serialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from scene.core.exceptions import ConfigurationError


_ROOT_REQUIRED_KEYS = {
    "schema_version",
    "project_name",
    "timezone",
    "paths",
    "storage",
    "sources",
}
_ROOT_OPTIONAL_KEYS = {
    "district_assignment",
    "miniature_dataset",
    "scene_generation",
}
_PATH_KEYS = {
    "project_root",
    "canonical_schema",
    "input_root",
    "external_root",
    "output_root",
    "reports_dir",
    "logs_dir",
    "metadata_dir",
    "resolved_config_dir",
    "tmp_dir",
}
_STORAGE_KEYS = {
    "geometry_format",
    "tabular_format",
    "parquet_compression",
    "resolved_config_format",
    "run_summary_format",
    "miniature_raster_format",
    "source_raster_policy",
    "geopackage_usage",
    "per_scene_pt_files",
    "training_cache_format",
}
_STORAGE_VALUES = {
    "geometry_format": "geopackage",
    "tabular_format": "parquet",
    "parquet_compression": "zstd",
    "resolved_config_format": "yaml",
    "run_summary_format": "json",
    "miniature_raster_format": "geotiff",
    "source_raster_policy": "read_only_reference",
    "geopackage_usage": "inspection_and_archive",
    "per_scene_pt_files": "forbidden",
    "training_cache_format": "open",
}
_SOURCE_REQUIRED_KEYS = {"source_name", "category", "kind", "path"}
_SOURCE_OPTIONAL_KEYS = {
    "administrative_level",
    "canonical_adapter",
    "expected_feature_count",
    "expected_geometry_type",
    "geographic_scope",
    "layer",
    "read_only",
    "source_crs",
    "source_format",
}
_SOURCE_KINDS = {"vector", "raster", "tabular"}


@dataclass(frozen=True, slots=True)
class PathConfig:
    """Resolved path roles for project inputs and outputs."""

    project_root: Path
    canonical_schema: Path
    input_root: Path
    external_root: Path
    output_root: Path
    reports_dir: Path
    logs_dir: Path
    metadata_dir: Path
    resolved_config_dir: Path
    tmp_dir: Path

    @property
    def read_only_roots(self) -> tuple[Path, Path]:
        return (self.input_root, self.external_root)

    @property
    def output_directories(self) -> tuple[Path, ...]:
        return (
            self.output_root,
            self.reports_dir,
            self.logs_dir,
            self.metadata_dir,
            self.resolved_config_dir,
            self.tmp_dir,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in sorted(_PATH_KEYS)
        }


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Approved D-011A storage choices and the D-011B open boundary."""

    geometry_format: str
    tabular_format: str
    parquet_compression: str
    resolved_config_format: str
    run_summary_format: str
    miniature_raster_format: str
    source_raster_policy: str
    geopackage_usage: str
    per_scene_pt_files: str
    training_cache_format: str

    def to_dict(self) -> dict[str, str]:
        return {
            key: getattr(self, key)
            for key in sorted(_STORAGE_KEYS)
        }


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One approved read-only source declared by configuration."""

    source_name: str
    category: str
    kind: str
    path: Path
    layer: str | None
    source_format: str | None = None
    source_crs: str | None = None
    administrative_level: str | None = None
    geographic_scope: str | None = None
    expected_geometry_type: str | None = None
    expected_feature_count: int | None = None
    read_only: bool = True
    canonical_adapter: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "administrative_level": self.administrative_level,
            "canonical_adapter": self.canonical_adapter,
            "category": self.category,
            "expected_feature_count": self.expected_feature_count,
            "expected_geometry_type": self.expected_geometry_type,
            "geographic_scope": self.geographic_scope,
            "kind": self.kind,
            "layer": self.layer,
            "path": str(self.path),
            "read_only": self.read_only,
            "source_crs": self.source_crs,
            "source_format": self.source_format,
            "source_name": self.source_name,
        }


@dataclass(frozen=True, slots=True)
class DistrictAssignmentConfig:
    """Approved D-005 implementation inputs and deterministic search settings."""

    assignment_version: str
    assignment_seed: int
    train_count: int
    validation_count: int
    test_count: int
    canonical_boundary_path: Path
    canonical_boundary_layer: str
    canonical_boundary_content_hash: str
    building_geometry_path: Path
    building_geometry_layer: str
    road_geometry_path: Path
    road_geometry_layer: str
    poi_geometry_path: Path
    poi_geometry_layer: str
    poi_attributes_path: Path
    landcover_source_name: str
    dem_source_name: str
    epsg: int
    scene_side_length_m: float
    scene_stride_m: float
    grid_origin_m: tuple[float, float]
    cross_split_buffer_m: float
    optimizer_algorithm_version: str
    candidate_count: int
    spatial_cluster_count: int
    context_cluster_count: int
    validation_test_min_context_clusters: int
    validation_test_component_min: int
    validation_test_component_max: int
    objective_weights: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "assignment_seed": self.assignment_seed,
            "assignment_version": self.assignment_version,
            "balancing_sources": {
                "building_geometry_layer": self.building_geometry_layer,
                "building_geometry_path": str(self.building_geometry_path),
                "dem_source_name": self.dem_source_name,
                "landcover_source_name": self.landcover_source_name,
                "poi_attributes_path": str(self.poi_attributes_path),
                "poi_geometry_layer": self.poi_geometry_layer,
                "poi_geometry_path": str(self.poi_geometry_path),
                "road_geometry_layer": self.road_geometry_layer,
                "road_geometry_path": str(self.road_geometry_path),
            },
            "canonical_boundary": {
                "content_hash": self.canonical_boundary_content_hash,
                "layer": self.canonical_boundary_layer,
                "path": str(self.canonical_boundary_path),
            },
            "counts": {
                "test": self.test_count,
                "train": self.train_count,
                "validation": self.validation_count,
            },
            "optimizer": {
                "algorithm_version": self.optimizer_algorithm_version,
                "candidate_count": self.candidate_count,
                "context_cluster_count": self.context_cluster_count,
                "objective_weights": dict(sorted(self.objective_weights.items())),
                "spatial_cluster_count": self.spatial_cluster_count,
                "validation_test_component_max": (
                    self.validation_test_component_max
                ),
                "validation_test_component_min": (
                    self.validation_test_component_min
                ),
                "validation_test_min_context_clusters": (
                    self.validation_test_min_context_clusters
                ),
            },
            "scene_policy": {
                "cross_split_buffer_m": self.cross_split_buffer_m,
                "epsg": self.epsg,
                "grid_origin_m": list(self.grid_origin_m),
                "scene_side_length_m": self.scene_side_length_m,
                "scene_stride_m": self.scene_stride_m,
            },
        }


@dataclass(frozen=True, slots=True)
class SceneGenerationConfig:
    """Approved D-018 through D-022 scene-footprint settings."""

    scene_generation_version: str
    assignment_lock_path: Path
    canonical_crs: str
    scene_width_m: float
    scene_height_m: float
    stride_x_m: float
    stride_y_m: float
    origin_x_m: float
    origin_y_m: float
    origin_anchor: str
    cross_split_exclusion_per_side_m: float
    minimum_allowable_region_distance_m: float
    eligibility_predicate: str
    boundary_touch_allowed: bool
    linear_tolerance_m: float
    area_tolerance_m2: float
    primary_district_rule: str
    primary_district_tie_break: str

    def to_dict(self) -> dict[str, object]:
        return {
            "area_tolerance_m2": self.area_tolerance_m2,
            "assignment_lock_path": str(self.assignment_lock_path),
            "boundary_touch_allowed": self.boundary_touch_allowed,
            "canonical_crs": self.canonical_crs,
            "cross_split_exclusion_per_side_m": (
                self.cross_split_exclusion_per_side_m
            ),
            "eligibility_predicate": self.eligibility_predicate,
            "linear_tolerance_m": self.linear_tolerance_m,
            "minimum_allowable_region_distance_m": (
                self.minimum_allowable_region_distance_m
            ),
            "origin_anchor": self.origin_anchor,
            "origin_x_m": self.origin_x_m,
            "origin_y_m": self.origin_y_m,
            "primary_district_rule": self.primary_district_rule,
            "primary_district_tie_break": self.primary_district_tie_break,
            "scene_generation_version": self.scene_generation_version,
            "scene_height_m": self.scene_height_m,
            "scene_width_m": self.scene_width_m,
            "stride_x_m": self.stride_x_m,
            "stride_y_m": self.stride_y_m,
        }


@dataclass(frozen=True, slots=True)
class MiniatureDatasetConfig:
    """M1.8 candidate-only integration fixture inputs."""

    miniature_version: str
    scene_geometry_path: Path
    scene_geometry_layer: str
    scene_summary_path: Path
    stable_ids_path: Path
    raster_metadata_path: Path
    source_inventory_path: Path
    canonical_manifest_path: Path
    road_node_geometry_layer: str
    scenes_per_split: int
    split_order: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_manifest_path": str(self.canonical_manifest_path),
            "miniature_version": self.miniature_version,
            "raster_metadata_path": str(self.raster_metadata_path),
            "road_node_geometry_layer": self.road_node_geometry_layer,
            "scene_geometry_layer": self.scene_geometry_layer,
            "scene_geometry_path": str(self.scene_geometry_path),
            "scene_summary_path": str(self.scene_summary_path),
            "scenes_per_split": self.scenes_per_split,
            "source_inventory_path": str(self.source_inventory_path),
            "split_order": list(self.split_order),
            "stable_ids_path": str(self.stable_ids_path),
        }


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Validated, fully resolved project configuration."""

    schema_version: str
    project_name: str
    timezone: str
    paths: PathConfig
    storage: StorageConfig
    sources: tuple[SourceConfig, ...]
    district_assignment: DistrictAssignmentConfig | None = None
    scene_generation: SceneGenerationConfig | None = None
    miniature_dataset: MiniatureDatasetConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "paths": self.paths.to_dict(),
            "project_name": self.project_name,
            "schema_version": self.schema_version,
            "sources": [
                source.to_dict()
                for source in sorted(
                    self.sources,
                    key=lambda item: item.source_name,
                )
            ],
            "storage": self.storage.to_dict(),
            "timezone": self.timezone,
        }
        if self.district_assignment is not None:
            value["district_assignment"] = self.district_assignment.to_dict()
        if self.scene_generation is not None:
            value["scene_generation"] = self.scene_generation.to_dict()
        if self.miniature_dataset is not None:
            value["miniature_dataset"] = self.miniature_dataset.to_dict()
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{context} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{context} keys must be strings")
    return value


def _validate_keys(
    value: Mapping[str, object],
    required: set[str],
    context: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ConfigurationError(
            f"{context} is missing required keys: {', '.join(missing)}"
        )
    if unknown:
        raise ConfigurationError(
            f"{context} contains unknown keys: {', '.join(unknown)}"
        )


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context} must be a non-empty string")
    return value.strip()


def _optional_string(
    value: Mapping[str, object],
    key: str,
    context: str,
) -> str | None:
    raw = value.get(key)
    return _string(raw, f"{context}.{key}") if raw is not None else None


def _resolve_path(raw: object, base_dir: Path, context: str) -> Path:
    value = _string(raw, context)
    if "\x00" in value:
        raise ConfigurationError(f"{context} contains a null byte")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ConfigurationError(
            f"{context} must be an integer >= {minimum}"
        )
    return value


def _number(value: object, context: str, *, minimum: float = 0.0) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) < minimum
    ):
        raise ConfigurationError(
            f"{context} must be a number >= {minimum}"
        )
    return float(value)


def _load_district_assignment(
    raw: object,
    base_dir: Path,
) -> DistrictAssignmentConfig:
    context = "district_assignment"
    value = _mapping(raw, context)
    required = {
        "assignment_seed",
        "assignment_version",
        "balancing_sources",
        "canonical_boundary",
        "counts",
        "optimizer",
        "scene_policy",
    }
    _validate_keys(value, required, context)

    boundary = _mapping(value["canonical_boundary"], f"{context}.canonical_boundary")
    _validate_keys(
        boundary,
        {"content_hash", "layer", "path"},
        f"{context}.canonical_boundary",
    )
    sources = _mapping(value["balancing_sources"], f"{context}.balancing_sources")
    source_keys = {
        "building_geometry_layer",
        "building_geometry_path",
        "dem_source_name",
        "landcover_source_name",
        "poi_attributes_path",
        "poi_geometry_layer",
        "poi_geometry_path",
        "road_geometry_layer",
        "road_geometry_path",
    }
    _validate_keys(sources, source_keys, f"{context}.balancing_sources")
    counts = _mapping(value["counts"], f"{context}.counts")
    _validate_keys(counts, {"test", "train", "validation"}, f"{context}.counts")
    scene = _mapping(value["scene_policy"], f"{context}.scene_policy")
    scene_keys = {
        "cross_split_buffer_m",
        "epsg",
        "grid_origin_m",
        "scene_side_length_m",
        "scene_stride_m",
    }
    _validate_keys(scene, scene_keys, f"{context}.scene_policy")
    origin = scene["grid_origin_m"]
    if (
        not isinstance(origin, list)
        or len(origin) != 2
        or any(
            not isinstance(item, (int, float)) or isinstance(item, bool)
            for item in origin
        )
    ):
        raise ConfigurationError(
            f"{context}.scene_policy.grid_origin_m must have two numbers"
        )
    optimizer = _mapping(value["optimizer"], f"{context}.optimizer")
    optimizer_keys = {
        "algorithm_version",
        "candidate_count",
        "context_cluster_count",
        "objective_weights",
        "spatial_cluster_count",
        "validation_test_component_max",
        "validation_test_component_min",
        "validation_test_min_context_clusters",
    }
    _validate_keys(optimizer, optimizer_keys, f"{context}.optimizer")
    raw_weights = _mapping(
        optimizer["objective_weights"],
        f"{context}.optimizer.objective_weights",
    )
    expected_weights = {
        "category_distribution",
        "context_diversity",
        "dem_distribution",
        "density_balance",
        "extensive_balance",
        "landcover_distribution",
        "spatial_diversity",
    }
    _validate_keys(
        raw_weights,
        expected_weights,
        f"{context}.optimizer.objective_weights",
    )
    weights = {
        key: _number(
            raw_weights[key],
            f"{context}.optimizer.objective_weights.{key}",
        )
        for key in expected_weights
    }
    content_hash = _string(
        boundary["content_hash"],
        f"{context}.canonical_boundary.content_hash",
    ).lower()
    if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
        raise ConfigurationError(
            f"{context}.canonical_boundary.content_hash must be SHA-256"
        )
    config = DistrictAssignmentConfig(
        assignment_version=_string(
            value["assignment_version"],
            f"{context}.assignment_version",
        ),
        assignment_seed=_integer(
            value["assignment_seed"],
            f"{context}.assignment_seed",
        ),
        train_count=_integer(counts["train"], f"{context}.counts.train", minimum=1),
        validation_count=_integer(
            counts["validation"],
            f"{context}.counts.validation",
            minimum=1,
        ),
        test_count=_integer(counts["test"], f"{context}.counts.test", minimum=1),
        canonical_boundary_path=_resolve_path(
            boundary["path"],
            base_dir,
            f"{context}.canonical_boundary.path",
        ),
        canonical_boundary_layer=_string(
            boundary["layer"],
            f"{context}.canonical_boundary.layer",
        ),
        canonical_boundary_content_hash=content_hash,
        building_geometry_path=_resolve_path(
            sources["building_geometry_path"],
            base_dir,
            f"{context}.balancing_sources.building_geometry_path",
        ),
        building_geometry_layer=_string(
            sources["building_geometry_layer"],
            f"{context}.balancing_sources.building_geometry_layer",
        ),
        road_geometry_path=_resolve_path(
            sources["road_geometry_path"],
            base_dir,
            f"{context}.balancing_sources.road_geometry_path",
        ),
        road_geometry_layer=_string(
            sources["road_geometry_layer"],
            f"{context}.balancing_sources.road_geometry_layer",
        ),
        poi_geometry_path=_resolve_path(
            sources["poi_geometry_path"],
            base_dir,
            f"{context}.balancing_sources.poi_geometry_path",
        ),
        poi_geometry_layer=_string(
            sources["poi_geometry_layer"],
            f"{context}.balancing_sources.poi_geometry_layer",
        ),
        poi_attributes_path=_resolve_path(
            sources["poi_attributes_path"],
            base_dir,
            f"{context}.balancing_sources.poi_attributes_path",
        ),
        landcover_source_name=_string(
            sources["landcover_source_name"],
            f"{context}.balancing_sources.landcover_source_name",
        ),
        dem_source_name=_string(
            sources["dem_source_name"],
            f"{context}.balancing_sources.dem_source_name",
        ),
        epsg=_integer(scene["epsg"], f"{context}.scene_policy.epsg", minimum=1),
        scene_side_length_m=_number(
            scene["scene_side_length_m"],
            f"{context}.scene_policy.scene_side_length_m",
            minimum=1.0,
        ),
        scene_stride_m=_number(
            scene["scene_stride_m"],
            f"{context}.scene_policy.scene_stride_m",
            minimum=1.0,
        ),
        grid_origin_m=(float(origin[0]), float(origin[1])),
        cross_split_buffer_m=_number(
            scene["cross_split_buffer_m"],
            f"{context}.scene_policy.cross_split_buffer_m",
            minimum=0.0,
        ),
        optimizer_algorithm_version=_string(
            optimizer["algorithm_version"],
            f"{context}.optimizer.algorithm_version",
        ),
        candidate_count=_integer(
            optimizer["candidate_count"],
            f"{context}.optimizer.candidate_count",
            minimum=1,
        ),
        spatial_cluster_count=_integer(
            optimizer["spatial_cluster_count"],
            f"{context}.optimizer.spatial_cluster_count",
            minimum=2,
        ),
        context_cluster_count=_integer(
            optimizer["context_cluster_count"],
            f"{context}.optimizer.context_cluster_count",
            minimum=2,
        ),
        validation_test_min_context_clusters=_integer(
            optimizer["validation_test_min_context_clusters"],
            f"{context}.optimizer.validation_test_min_context_clusters",
            minimum=1,
        ),
        validation_test_component_min=_integer(
            optimizer["validation_test_component_min"],
            f"{context}.optimizer.validation_test_component_min",
            minimum=1,
        ),
        validation_test_component_max=_integer(
            optimizer["validation_test_component_max"],
            f"{context}.optimizer.validation_test_component_max",
            minimum=1,
        ),
        objective_weights=weights,
    )
    if config.train_count + config.validation_count + config.test_count != 25:
        raise ConfigurationError(
            "district_assignment counts must sum to 25"
        )
    if config.assignment_seed != 20260723:
        raise ConfigurationError(
            "district_assignment.assignment_seed must be 20260723"
        )
    if (
        config.epsg != 5186
        or config.scene_side_length_m != 500.0
        or config.scene_stride_m != 250.0
        or config.grid_origin_m != (0.0, 0.0)
        or config.cross_split_buffer_m != 250.0
    ):
        raise ConfigurationError(
            "district_assignment scene policy must match D-005"
        )
    if (
        config.validation_test_component_min
        > config.validation_test_component_max
    ):
        raise ConfigurationError(
            "district assignment component bounds are inverted"
        )
    return config


def _load_scene_generation(
    raw: object,
    base_dir: Path,
) -> SceneGenerationConfig:
    context = "scene_generation"
    value = _mapping(raw, context)
    keys = {
        "area_tolerance_m2",
        "assignment_lock_path",
        "boundary_touch_allowed",
        "canonical_crs",
        "cross_split_exclusion_per_side_m",
        "eligibility_predicate",
        "linear_tolerance_m",
        "minimum_allowable_region_distance_m",
        "origin_anchor",
        "origin_x_m",
        "origin_y_m",
        "primary_district_rule",
        "primary_district_tie_break",
        "scene_generation_version",
        "scene_height_m",
        "scene_width_m",
        "stride_x_m",
        "stride_y_m",
    }
    _validate_keys(value, keys, context)
    boundary_touch = value["boundary_touch_allowed"]
    if not isinstance(boundary_touch, bool):
        raise ConfigurationError(
            "scene_generation.boundary_touch_allowed must be boolean"
        )
    config = SceneGenerationConfig(
        scene_generation_version=_string(
            value["scene_generation_version"],
            f"{context}.scene_generation_version",
        ),
        assignment_lock_path=_resolve_path(
            value["assignment_lock_path"],
            base_dir,
            f"{context}.assignment_lock_path",
        ),
        canonical_crs=_string(
            value["canonical_crs"],
            f"{context}.canonical_crs",
        ),
        scene_width_m=_number(
            value["scene_width_m"],
            f"{context}.scene_width_m",
            minimum=1.0,
        ),
        scene_height_m=_number(
            value["scene_height_m"],
            f"{context}.scene_height_m",
            minimum=1.0,
        ),
        stride_x_m=_number(
            value["stride_x_m"],
            f"{context}.stride_x_m",
            minimum=1.0,
        ),
        stride_y_m=_number(
            value["stride_y_m"],
            f"{context}.stride_y_m",
            minimum=1.0,
        ),
        origin_x_m=_number(
            value["origin_x_m"],
            f"{context}.origin_x_m",
        ),
        origin_y_m=_number(
            value["origin_y_m"],
            f"{context}.origin_y_m",
        ),
        origin_anchor=_string(
            value["origin_anchor"],
            f"{context}.origin_anchor",
        ),
        cross_split_exclusion_per_side_m=_number(
            value["cross_split_exclusion_per_side_m"],
            f"{context}.cross_split_exclusion_per_side_m",
        ),
        minimum_allowable_region_distance_m=_number(
            value["minimum_allowable_region_distance_m"],
            f"{context}.minimum_allowable_region_distance_m",
        ),
        eligibility_predicate=_string(
            value["eligibility_predicate"],
            f"{context}.eligibility_predicate",
        ),
        boundary_touch_allowed=boundary_touch,
        linear_tolerance_m=_number(
            value["linear_tolerance_m"],
            f"{context}.linear_tolerance_m",
        ),
        area_tolerance_m2=_number(
            value["area_tolerance_m2"],
            f"{context}.area_tolerance_m2",
        ),
        primary_district_rule=_string(
            value["primary_district_rule"],
            f"{context}.primary_district_rule",
        ),
        primary_district_tie_break=_string(
            value["primary_district_tie_break"],
            f"{context}.primary_district_tie_break",
        ),
    )
    expected: dict[str, object] = {
        "area_tolerance_m2": 1.0e-6,
        "boundary_touch_allowed": True,
        "canonical_crs": "EPSG:5186",
        "cross_split_exclusion_per_side_m": 125.0,
        "eligibility_predicate": "covers",
        "linear_tolerance_m": 1.0e-8,
        "minimum_allowable_region_distance_m": 250.0,
        "origin_anchor": "center",
        "origin_x_m": 0.0,
        "origin_y_m": 0.0,
        "primary_district_rule": "largest_intersection_area",
        "primary_district_tie_break": "district_code_ascending",
        "scene_generation_version": "scene-footprint-v1",
        "scene_height_m": 500.0,
        "scene_width_m": 500.0,
        "stride_x_m": 250.0,
        "stride_y_m": 250.0,
    }
    actual = config.to_dict()
    mismatches = {
        key: (actual[key], expected_value)
        for key, expected_value in expected.items()
        if actual[key] != expected_value
    }
    if mismatches:
        raise ConfigurationError(
            f"scene_generation does not match D-018 through D-022: {mismatches}"
        )
    return config


def _load_miniature_dataset(
    raw: object,
    base_dir: Path,
) -> MiniatureDatasetConfig:
    context = "miniature_dataset"
    value = _mapping(raw, context)
    keys = {
        "canonical_manifest_path",
        "miniature_version",
        "raster_metadata_path",
        "road_node_geometry_layer",
        "scene_geometry_layer",
        "scene_geometry_path",
        "scene_summary_path",
        "scenes_per_split",
        "source_inventory_path",
        "split_order",
        "stable_ids_path",
    }
    _validate_keys(value, keys, context)
    raw_split_order = value["split_order"]
    if (
        not isinstance(raw_split_order, list)
        or any(not isinstance(item, str) for item in raw_split_order)
    ):
        raise ConfigurationError(
            "miniature_dataset.split_order must be a list of strings"
        )
    split_order = tuple(item.strip().lower() for item in raw_split_order)
    if split_order != ("train", "validation", "test"):
        raise ConfigurationError(
            "miniature_dataset.split_order must be "
            "['train', 'validation', 'test']"
        )
    config = MiniatureDatasetConfig(
        miniature_version=_string(
            value["miniature_version"],
            f"{context}.miniature_version",
        ),
        scene_geometry_path=_resolve_path(
            value["scene_geometry_path"],
            base_dir,
            f"{context}.scene_geometry_path",
        ),
        scene_geometry_layer=_string(
            value["scene_geometry_layer"],
            f"{context}.scene_geometry_layer",
        ),
        scene_summary_path=_resolve_path(
            value["scene_summary_path"],
            base_dir,
            f"{context}.scene_summary_path",
        ),
        stable_ids_path=_resolve_path(
            value["stable_ids_path"],
            base_dir,
            f"{context}.stable_ids_path",
        ),
        raster_metadata_path=_resolve_path(
            value["raster_metadata_path"],
            base_dir,
            f"{context}.raster_metadata_path",
        ),
        source_inventory_path=_resolve_path(
            value["source_inventory_path"],
            base_dir,
            f"{context}.source_inventory_path",
        ),
        canonical_manifest_path=_resolve_path(
            value["canonical_manifest_path"],
            base_dir,
            f"{context}.canonical_manifest_path",
        ),
        road_node_geometry_layer=_string(
            value["road_node_geometry_layer"],
            f"{context}.road_node_geometry_layer",
        ),
        scenes_per_split=_integer(
            value["scenes_per_split"],
            f"{context}.scenes_per_split",
            minimum=1,
        ),
        split_order=split_order,
    )
    if config.miniature_version != "miniature-candidates-v1":
        raise ConfigurationError(
            "miniature_dataset.miniature_version must be "
            "'miniature-candidates-v1'"
        )
    if config.scenes_per_split != 3:
        raise ConfigurationError(
            "miniature_dataset.scenes_per_split must be 3"
        )
    return config


def _load_sources(
    raw: object,
    input_root: Path,
) -> tuple[SourceConfig, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError("sources must be a list")

    sources: list[SourceConfig] = []
    names: set[str] = set()
    allowed_keys = _SOURCE_REQUIRED_KEYS | _SOURCE_OPTIONAL_KEYS
    for index, item in enumerate(raw):
        context = f"sources[{index}]"
        value = _mapping(item, context)
        missing = sorted(_SOURCE_REQUIRED_KEYS - set(value))
        unknown = sorted(set(value) - allowed_keys)
        if missing:
            raise ConfigurationError(
                f"{context} is missing required keys: {', '.join(missing)}"
            )
        if unknown:
            raise ConfigurationError(
                f"{context} contains unknown keys: {', '.join(unknown)}"
            )

        source_name = _string(value["source_name"], f"{context}.source_name")
        if re.fullmatch(r"[a-z][a-z0-9_]*", source_name) is None:
            raise ConfigurationError(
                f"{context}.source_name must use lowercase snake_case"
            )
        if source_name in names:
            raise ConfigurationError(f"duplicate source_name: {source_name}")
        names.add(source_name)

        category = _string(value["category"], f"{context}.category").lower()
        kind = _string(value["kind"], f"{context}.kind").lower()
        if kind not in _SOURCE_KINDS:
            raise ConfigurationError(
                f"{context}.kind must be one of: "
                f"{', '.join(sorted(_SOURCE_KINDS))}"
            )

        layer_value = value.get("layer")
        layer = (
            _string(layer_value, f"{context}.layer")
            if layer_value is not None
            else None
        )
        if kind == "vector" and layer is None:
            raise ConfigurationError(f"{context}.layer is required for vector")
        if kind != "vector" and layer is not None:
            raise ConfigurationError(
                f"{context}.layer is only allowed for vector sources"
            )

        read_only_raw = value.get("read_only", True)
        if not isinstance(read_only_raw, bool):
            raise ConfigurationError(f"{context}.read_only must be boolean")
        source_path = _resolve_path(
            value["path"],
            input_root,
            f"{context}.path",
        )
        explicitly_external_read_only = (
            value.get("read_only") is True and source_path.is_absolute()
        )
        if (
            not source_path.is_relative_to(input_root)
            and not explicitly_external_read_only
        ):
            raise ConfigurationError(
                f"{context}.path must be inside paths.input_root unless an "
                "absolute external source is explicitly read_only"
            )
        expected_feature_count = value.get("expected_feature_count")
        if expected_feature_count is not None and (
            not isinstance(expected_feature_count, int)
            or isinstance(expected_feature_count, bool)
            or expected_feature_count < 0
        ):
            raise ConfigurationError(
                f"{context}.expected_feature_count must be a non-negative integer"
            )
        sources.append(
            SourceConfig(
                source_name=source_name,
                category=category,
                kind=kind,
                path=source_path,
                layer=layer,
                source_format=_optional_string(value, "source_format", context),
                source_crs=_optional_string(value, "source_crs", context),
                administrative_level=_optional_string(
                    value, "administrative_level", context
                ),
                geographic_scope=_optional_string(
                    value, "geographic_scope", context
                ),
                expected_geometry_type=_optional_string(
                    value, "expected_geometry_type", context
                ),
                expected_feature_count=expected_feature_count,
                read_only=read_only_raw,
                canonical_adapter=_optional_string(
                    value, "canonical_adapter", context
                ),
            )
        )
    return tuple(sources)


def load_config(config_path: str | Path) -> ProjectConfig:
    """Load and strictly validate YAML without creating files or directories."""

    path = Path(config_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ConfigurationError(f"configuration file does not exist: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read configuration {path}: {exc}") from exc

    root = _mapping(raw, "configuration")
    missing = sorted(_ROOT_REQUIRED_KEYS - set(root))
    unknown = sorted(
        set(root) - (_ROOT_REQUIRED_KEYS | _ROOT_OPTIONAL_KEYS)
    )
    if missing:
        raise ConfigurationError(
            f"configuration is missing required keys: {', '.join(missing)}"
        )
    if unknown:
        raise ConfigurationError(
            f"configuration contains unknown keys: {', '.join(unknown)}"
        )

    schema_version = _string(root["schema_version"], "schema_version")
    project_name = _string(root["project_name"], "project_name")
    timezone = _string(root["timezone"], "timezone")
    if timezone != "Asia/Seoul":
        raise ConfigurationError("timezone must be Asia/Seoul")

    path_values = _mapping(root["paths"], "paths")
    _validate_keys(path_values, _PATH_KEYS, "paths")
    base_dir = path.parent
    paths = PathConfig(
        **{
            key: _resolve_path(path_values[key], base_dir, f"paths.{key}")
            for key in _PATH_KEYS
        }
    )

    storage_values = _mapping(root["storage"], "storage")
    _validate_keys(storage_values, _STORAGE_KEYS, "storage")
    normalized_storage = {
        key: _string(storage_values[key], f"storage.{key}").lower()
        for key in _STORAGE_KEYS
    }
    for key, expected in _STORAGE_VALUES.items():
        if normalized_storage[key] != expected:
            raise ConfigurationError(
                f"storage.{key} must be {expected!r}, "
                f"got {normalized_storage[key]!r}"
            )

    sources = _load_sources(root["sources"], paths.input_root)
    district_assignment = (
        _load_district_assignment(root["district_assignment"], base_dir)
        if root.get("district_assignment") is not None
        else None
    )
    scene_generation = (
        _load_scene_generation(root["scene_generation"], base_dir)
        if root.get("scene_generation") is not None
        else None
    )
    miniature_dataset = (
        _load_miniature_dataset(root["miniature_dataset"], base_dir)
        if root.get("miniature_dataset") is not None
        else None
    )
    return ProjectConfig(
        schema_version=schema_version,
        project_name=project_name,
        timezone=timezone,
        paths=paths,
        storage=StorageConfig(**normalized_storage),
        sources=sources,
        district_assignment=district_assignment,
        scene_generation=scene_generation,
        miniature_dataset=miniature_dataset,
    )


def write_resolved_config(config: ProjectConfig, destination: str | Path) -> Path:
    """Write a canonical YAML snapshot with its deterministic SHA-256."""

    path = Path(destination)
    payload = {
        "resolved_config": config.to_dict(),
        "resolved_config_hash": config.canonical_hash,
    }
    serialized = yaml.safe_dump(
        payload,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise ConfigurationError(
            f"cannot write resolved configuration {path}: {exc}"
        ) from exc
    return path
