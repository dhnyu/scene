from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scene.observations.exceptions import ObservationContractError
from scene.observations.schema import (
    EXPECTED_COMMON_COLUMNS,
    EXPECTED_VALUE_STATES,
    load_observation_schema,
)


def test_observation_schema_contract(project_root: Path) -> None:
    schema = load_observation_schema(
        project_root / "docs" / "contracts" / "scene_observation_schema.yaml"
    )

    assert schema.schema_version == "m2.1-v1"
    assert schema.canonical_crs == "EPSG:5186"
    assert schema.common_columns == EXPECTED_COMMON_COLUMNS
    assert schema.value_states == EXPECTED_VALUE_STATES
    assert schema.partition_order == ("split", "district_id", "shard_id")
    assert schema.raw["observation_id_spec"] == {
        "hash_algorithm": "SHA-256",
        "serialization": "ordered_fields_joined_by_separator",
        "separator": "|",
        "encoding": "UTF-8",
        "output_format": "lowercase_hex_digest",
        "output_length": 64,
        "building_fields": ["scene_id", "object_type", "object_id"],
        "poi_fields": ["scene_id", "object_type", "object_id"],
        "road_fields": ["scene_id", "object_type", "object_id", "part_id"],
    }
    assert len(schema.sha256) == 64


def test_observation_schema_rejects_changed_identity(
    project_root: Path,
    tmp_path: Path,
) -> None:
    source = (
        project_root / "docs" / "contracts" / "scene_observation_schema.yaml"
    )
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["identity"]["road"]["fields"] = [
        "scene_id",
        "object_type",
        "object_id",
    ]
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ObservationContractError, match="fields mismatch"):
        load_observation_schema(changed)


def test_observation_schema_distinguishes_required_value_states(
    project_root: Path,
) -> None:
    schema = load_observation_schema(
        project_root / "docs" / "contracts" / "scene_observation_schema.yaml"
    )
    required = {
        "missing",
        "not_applicable",
        "raster_nodata",
        "observed_false",
    }

    assert required.issubset(schema.value_states)
    assert len(required) == len(set(required))
