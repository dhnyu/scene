from __future__ import annotations

import hashlib

import pytest

from scene.id.exceptions import StableIdGenerationError
from scene.id.generator import DerivedIdFactory


def test_building_and_poi_observation_ids_are_scene_specific() -> None:
    building_a = DerivedIdFactory.observation_id(
        "scene-a",
        "building",
        "building-1",
    )
    building_b = DerivedIdFactory.observation_id(
        "scene-b",
        "building",
        "building-1",
    )
    poi = DerivedIdFactory.observation_id("scene-a", "poi", "poi-1")

    assert building_a == DerivedIdFactory.observation_id(
        "scene-a",
        "building",
        "building-1",
    )
    assert building_a == hashlib.sha256(
        b"scene-a|building|building-1"
    ).hexdigest()
    assert building_a != building_b
    assert building_a != poi


def test_road_observation_id_requires_and_uses_part_id() -> None:
    first = DerivedIdFactory.observation_id(
        "scene-a",
        "road",
        "road-1",
        "part-1",
    )
    second = DerivedIdFactory.observation_id(
        "scene-a",
        "road",
        "road-1",
        "part-2",
    )

    assert first != second
    assert first == hashlib.sha256(
        b"scene-a|road|road-1|part-1"
    ).hexdigest()
    with pytest.raises(StableIdGenerationError, match="requires"):
        DerivedIdFactory.observation_id("scene-a", "road", "road-1")


def test_nonroad_observation_rejects_part_id() -> None:
    with pytest.raises(StableIdGenerationError, match="must be null"):
        DerivedIdFactory.observation_id(
            "scene-a",
            "building",
            "building-1",
            "part-1",
        )
