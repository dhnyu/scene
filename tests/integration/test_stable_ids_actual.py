from __future__ import annotations

from pathlib import Path

from scene.id.generator import StableIdGenerator
from scene.id.reader import StableIdReader, find_latest_canonical_manifest
from scene.id.validator import StableIdValidator
from scene.schema.schema import load_canonical_schema


def test_actual_canonical_dataset_stable_ids() -> None:
    project = Path(__file__).resolve().parents[2]
    schema = load_canonical_schema(
        project / "docs/contracts/canonical_schema.yaml"
    )
    source = StableIdReader(schema, project / "outputs").read(
        find_latest_canonical_manifest(project / "outputs")
    )
    generator = StableIdGenerator()
    dataset = generator.generate(
        source,
        run_id="20260724_080000_KST",
        config_hash="0" * 64,
    )
    digest = generator.regeneration_digest(
        source,
        run_id="20260724_080000_KST",
        config_hash="0" * 64,
    )
    validation = StableIdValidator().validate(
        dataset,
        regeneration_digest=digest,
    )
    assert validation.valid
    assert validation.counts == {
        "building": 723875,
        "road_link": 66854,
        "road_node": 48748,
        "poi": 498828,
    }
