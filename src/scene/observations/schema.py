"""Strict loader for the M2.1 machine-readable observation schema."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from scene.observations.exceptions import ObservationContractError


EXPECTED_COMMON_COLUMNS = (
    "release_id",
    "split",
    "district_id",
    "scene_id",
    "object_type",
    "object_id",
    "part_id",
    "observation_id",
    "source_name",
    "geometry_status",
    "touches_scene_boundary",
    "representative_x",
    "representative_y",
)
EXPECTED_VALUE_STATES = (
    "present",
    "missing",
    "not_applicable",
    "raster_nodata",
    "observed_false",
)
EXPECTED_OBJECT_TYPES = ("building", "road", "poi")
EXPECTED_GEOMETRY_STATUS = ("full", "clipped", "split_by_clip")
EXPECTED_ENTITIES = {
    "building_observation",
    "road_observation",
    "poi_observation",
    "object_raster_observation",
    "scene_raster_observation",
    "observation_value_state",
}


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObservationContractError(f"{label} must be a mapping")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ObservationContractError(
            f"{label} must be a list of non-empty strings"
        )
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ObservationSchema:
    """Validated schema plus a content hash."""

    path: Path
    schema_name: str
    schema_version: str
    canonical_crs: str
    common_columns: tuple[str, ...]
    value_states: tuple[str, ...]
    entities: tuple[str, ...]
    partition_order: tuple[str, ...]
    sha256: str
    raw: Mapping[str, Any]


def load_observation_schema(path: str | Path) -> ObservationSchema:
    """Load and validate all values fixed by the M2.1 contract."""

    schema_path = Path(path).expanduser().resolve()
    try:
        content = schema_path.read_bytes()
        data = yaml.safe_load(content)
    except (OSError, yaml.YAMLError) as exc:
        raise ObservationContractError(
            f"cannot load observation schema {schema_path}: {exc}"
        ) from exc
    root = _mapping(data, "observation schema")

    if root.get("schema_name") != "scene_observation":
        raise ObservationContractError(
            "schema_name must be scene_observation"
        )
    if root.get("schema_version") != "m2.1-v1":
        raise ObservationContractError(
            "schema_version must be m2.1-v1"
        )
    if root.get("canonical_crs") != "EPSG:5186":
        raise ObservationContractError(
            "canonical_crs must be EPSG:5186"
        )

    category_order = _mapping(root.get("category_order"), "category_order")
    object_types = _string_list(
        category_order.get("object_type"),
        "category_order.object_type",
    )
    geometry_status = _string_list(
        category_order.get("geometry_status"),
        "category_order.geometry_status",
    )
    value_states = _string_list(
        category_order.get("value_state"),
        "category_order.value_state",
    )
    if object_types != EXPECTED_OBJECT_TYPES:
        raise ObservationContractError("object_type category order mismatch")
    if geometry_status != EXPECTED_GEOMETRY_STATUS:
        raise ObservationContractError(
            "geometry_status category order mismatch"
        )
    if value_states != EXPECTED_VALUE_STATES:
        raise ObservationContractError("value_state category order mismatch")

    common = root.get("common_vector_columns")
    if not isinstance(common, list):
        raise ObservationContractError(
            "common_vector_columns must be a list"
        )
    common_columns = tuple(
        str(_mapping(item, "common column").get("column"))
        for item in common
    )
    if common_columns != EXPECTED_COMMON_COLUMNS:
        raise ObservationContractError(
            "common vector column order mismatch"
        )
    if len(common_columns) != len(set(common_columns)):
        raise ObservationContractError(
            "common vector columns must be unique"
        )

    identity = _mapping(root.get("identity"), "identity")
    observation_id_spec = _mapping(
        root.get("observation_id_spec"),
        "observation_id_spec",
    )
    expected_id_spec = {
        "hash_algorithm": "SHA-256",
        "serialization": "ordered_fields_joined_by_separator",
        "separator": "|",
        "encoding": "UTF-8",
        "output_format": "lowercase_hex_digest",
        "output_length": 64,
    }
    for key, expected in expected_id_spec.items():
        if observation_id_spec.get(key) != expected:
            raise ObservationContractError(
                f"observation_id_spec.{key} must be {expected}"
            )
    expected_identity = {
        "building": ("scene_id", "object_type", "object_id"),
        "poi": ("scene_id", "object_type", "object_id"),
        "road": ("scene_id", "object_type", "object_id", "part_id"),
    }
    for object_type, expected_fields in expected_identity.items():
        spec = _mapping(identity.get(object_type), f"identity.{object_type}")
        if spec.get("hash_domain") != "observation_id":
            raise ObservationContractError(
                f"{object_type} hash_domain must be observation_id"
            )
        fields = _string_list(
            spec.get("fields"),
            f"identity.{object_type}.fields",
        )
        if fields != expected_fields:
            raise ObservationContractError(
                f"{object_type} observation ID fields mismatch"
            )
        if tuple(observation_id_spec.get(f"{object_type}_fields", ())) != fields:
            raise ObservationContractError(
                f"observation_id_spec.{object_type}_fields mismatch"
            )

    entities = _mapping(root.get("entities"), "entities")
    if set(entities) != EXPECTED_ENTITIES:
        raise ObservationContractError("observation entity set mismatch")

    storage = _mapping(root.get("storage"), "storage")
    partition_order = _string_list(
        storage.get("partition_order"),
        "storage.partition_order",
    )
    if partition_order != ("split", "district_id", "shard_id"):
        raise ObservationContractError("partition order mismatch")
    if storage.get("parquet_compression") != "zstd":
        raise ObservationContractError(
            "Parquet compression must be zstd"
        )
    if storage.get("per_scene_pt_files") != "forbidden":
        raise ObservationContractError(
            "per-scene pt files must be forbidden"
        )

    validation = _mapping(root.get("validation"), "validation")
    expected_validation = {
        "invalid_geometry": "hard_failure",
        "geometry_collection": "hard_failure",
        "geometry_collection_empty": "hard_failure",
        "geometry_repair": "forbidden",
        "coordinate_rounding": "forbidden",
        "simplification": "forbidden",
        "precision_reduction": "forbidden",
        "poi_predicate": "scene.covers(point)",
    }
    for key, expected in expected_validation.items():
        if validation.get(key) != expected:
            raise ObservationContractError(
                f"validation.{key} must be {expected}"
            )

    return ObservationSchema(
        path=schema_path,
        schema_name="scene_observation",
        schema_version="m2.1-v1",
        canonical_crs="EPSG:5186",
        common_columns=common_columns,
        value_states=value_states,
        entities=tuple(sorted(entities)),
        partition_order=partition_order,
        sha256=hashlib.sha256(content).hexdigest(),
        raw=root,
    )
