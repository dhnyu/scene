from __future__ import annotations

from pathlib import Path

from conftest import make_stable_id_canonical_fixture
from scene.id.generator import (
    DerivedIdFactory,
    StableIdGenerator,
    building_id,
    canonical_hash,
    road_link_id,
    road_node_id,
)
from scene.id.reader import StableIdReader
from scene.id.validator import DerivedIdValidator
from scene.schema.schema import load_canonical_schema


def _dataset(tmp_path: Path, schema_path: Path):
    config_path, manifest_path = make_stable_id_canonical_fixture(
        tmp_path,
        schema_path,
    )
    schema = load_canonical_schema(schema_path)
    source = StableIdReader(schema, tmp_path / "outputs").read(manifest_path)
    dataset = StableIdGenerator().generate(
        source,
        run_id="20260724_070000_KST",
        config_hash="f" * 64,
    )
    return config_path, source, dataset


def test_source_ids_are_deterministic_and_preserve_native_text(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    _, source, dataset = _dataset(tmp_path, canonical_schema_path)
    generator = StableIdGenerator()
    regenerated = generator.regeneration_digest(
        source,
        run_id="20260724_070000_KST",
        config_hash="f" * 64,
    )
    assert dataset.generation_digest == regenerated
    assert dataset.ids["source_native_id"][0].as_py() == "0001"
    assert building_id("0001") == building_id("0001")
    assert building_id("0001") != building_id("1")
    assert road_link_id("0001") != road_node_id("0001")


def test_length_prefix_serialization_avoids_field_boundary_collision() -> None:
    assert canonical_hash("ab", "c") != canonical_hash("a", "bc")
    assert canonical_hash("", "x") != canonical_hash(None, "x")


def test_future_factories_are_deterministic_without_materialization() -> None:
    footprint = DerivedIdFactory.scene_footprint_id(
        "EPSG:5186",
        200000,
        550000,
        500,
        0,
        0,
    )
    assert footprint == DerivedIdFactory.scene_footprint_id(
        "EPSG:5186",
        "200000.0",
        "550000.00",
        "500.0",
        "0.0",
        "-0",
    )
    assert DerivedIdFactory.scene_id(footprint) == footprint


def test_clip_component_order_does_not_change_ids() -> None:
    components = (
        ("Polygon", b"component-a"),
        ("Polygon", b"component-b"),
        ("Polygon", b"component-a"),
    )
    forward = DerivedIdFactory.clip_part_ids(components)
    reverse = DerivedIdFactory.clip_part_ids(reversed(components))
    assert forward == reverse
    assert len(forward) == len(set(forward)) == 3


def test_original_and_augmented_relation_ids_are_disjoint() -> None:
    contexts = (
        DerivedIdFactory.relation_context_id(
            "scene",
            "original",
            None,
            "geometry-v1",
        ),
        DerivedIdFactory.relation_context_id(
            "scene",
            "augmented",
            1,
            "geometry-v1",
        ),
        DerivedIdFactory.relation_context_id(
            "scene",
            "augmented",
            2,
            "geometry-v1",
        ),
    )
    relations = {
        DerivedIdFactory.relation_id(
            context,
            "source",
            "target",
            "SN",
        )
        for context in contexts
    }
    assert len(set(contexts)) == 3
    assert len(relations) == 3
    assert DerivedIdValidator.relation_views_are_disjoint(
        scene_id="scene",
        geometry_version="geometry-v1",
        src_scene_object_id="source",
        dst_scene_object_id="target",
        relation_type="SN",
    )


def test_derived_id_validator_checks_scene_and_clip_invariants() -> None:
    assert DerivedIdValidator.scene_identity_is_deterministic(
        epsg="EPSG:5186",
        center_x=200000,
        center_y=550000,
        side_length_m=500,
        grid_origin_x=0,
        grid_origin_y=0,
    )
    assert DerivedIdValidator.clip_component_order_is_invariant(
        (
            ("Polygon", b"component-a"),
            ("Polygon", b"component-b"),
        )
    )
