from __future__ import annotations

from pathlib import Path

from scene.observations.reference import validate_fixture
from scene.observations.schema import load_observation_schema


def _validation(project_root: Path):
    schema = load_observation_schema(
        project_root / "docs" / "contracts" / "scene_observation_schema.yaml"
    )
    return validate_fixture(
        schema,
        project_root
        / "tests"
        / "fixtures"
        / "observations"
        / "m2_1_scene_observation_fixture.yaml",
    )


def test_fixture_expected_output_and_determinism(project_root: Path) -> None:
    result = _validation(project_root)

    assert result.valid
    assert result.expected_output_match
    assert result.deterministic_regeneration
    assert result.observation_count == 16
    assert result.source_access is False
    assert len(result.content_hash) == 64


def test_building_clip_boundary_and_multipart_rules(project_root: Path) -> None:
    records = _validation(project_root).records
    scene_a = "a" * 64
    selected = {
        (record.scene_id, record.object_id): record
        for record in records
        if record.object_type == "building"
    }

    clipped = selected[(scene_a, "building-overlap-scenes")]
    assert clipped.geometry_status == "clipped"
    assert clipped.observation_area_m2 == 8.0
    assert clipped.touches_scene_boundary is True
    assert clipped.part_id is None
    assert selected[(scene_a, "building-multipart")].observation_area_m2 == 2.0
    weighted = selected[(scene_a, "building-multipart-weighted")]
    assert weighted.observation_area_m2 == 5.0
    assert weighted.representative_x == 2.5
    assert weighted.representative_y == 4.3
    assert (scene_a, "building-boundary-touch") not in selected


def test_road_part_order_and_clipped_measure(project_root: Path) -> None:
    records = _validation(project_root).records
    parts = [
        record
        for record in records
        if record.scene_id == "a" * 64
        and record.object_id == "road-multipart"
    ]

    assert [record.part_order for record in parts] == [0, 1]
    assert [record.observation_length_m for record in parts] == [4.0, 4.0]
    assert all(record.geometry_status == "split_by_clip" for record in parts)
    assert all(record.parent_way_id == "synthetic-parent-2" for record in parts)
    assert all(record.is_scene_boundary_endpoint for record in parts)
    assert len({record.part_id for record in parts}) == 2
    assert len({record.observation_id for record in parts}) == 2


def test_poi_closed_set_and_overlapping_scene_identity(
    project_root: Path,
) -> None:
    result = _validation(project_root)
    poi_boundary = [
        record
        for record in result.records
        if record.object_id == "poi-boundary"
    ]
    outside = [
        record
        for record in result.records
        if record.object_id == "poi-outside"
    ]

    assert len(poi_boundary) == 2
    assert any(record.touches_scene_boundary for record in poi_boundary)
    assert outside == []
    assert result.overlapping_object_distinct_observation_ids


def test_invalid_and_geometry_collection_are_hard_failures(
    project_root: Path,
) -> None:
    result = _validation(project_root)

    assert result.invalid_geometry_hard_failures == 1
    assert result.geometry_collection_hard_failures == 2


def test_reference_evaluator_has_no_repair_or_precision_operation(
    project_root: Path,
) -> None:
    source = (
        project_root / "src" / "scene" / "observations" / "reference.py"
    ).read_text(encoding="utf-8")

    assert "make_valid" not in source
    assert ".buffer(" not in source
    assert ".simplify(" not in source
    assert "set_precision" not in source
