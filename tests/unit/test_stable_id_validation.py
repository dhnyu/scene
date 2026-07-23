from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa

from conftest import make_stable_id_canonical_fixture
from scene.id.generator import StableIdGenerator
from scene.id.reader import StableIdReader
from scene.id.validator import StableIdValidator
from scene.schema.schema import load_canonical_schema


def _generated(tmp_path: Path, schema_path: Path):
    _, manifest = make_stable_id_canonical_fixture(tmp_path, schema_path)
    schema = load_canonical_schema(schema_path)
    source = StableIdReader(schema, tmp_path / "outputs").read(manifest)
    generator = StableIdGenerator()
    dataset = generator.generate(
        source,
        run_id="20260724_070000_KST",
        config_hash="f" * 64,
    )
    digest = generator.regeneration_digest(
        source,
        run_id="20260724_070000_KST",
        config_hash="f" * 64,
    )
    return dataset, digest


def test_valid_ids_and_provenance_pass(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    dataset, digest = _generated(tmp_path, canonical_schema_path)
    result = StableIdValidator().validate(
        dataset,
        regeneration_digest=digest,
    )
    assert result.valid
    assert result.global_duplicate_id_count == 0
    assert result.global_null_id_count == 0
    assert result.provenance_complete
    assert result.source_canonical_mapping_valid
    assert result.counts == {
        "building": 2,
        "road_link": 1,
        "road_node": 1,
        "poi": 2,
    }


def test_duplicate_ids_are_reported(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    dataset, digest = _generated(tmp_path, canonical_schema_path)
    duplicate = pa.concat_tables([dataset.ids, dataset.ids.slice(0, 1)])
    provenance = pa.concat_tables(
        [dataset.provenance, dataset.provenance.slice(0, 1)]
    )
    result = StableIdValidator().validate(
        replace(dataset, ids=duplicate, provenance=provenance),
        regeneration_digest=digest,
    )
    assert not result.valid
    assert result.global_duplicate_id_count == 1


def test_null_ids_and_incomplete_provenance_are_reported(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    dataset, digest = _generated(tmp_path, canonical_schema_path)
    ids = dataset.ids.set_column(
        dataset.ids.schema.get_field_index("canonical_object_id"),
        dataset.ids.schema.field("canonical_object_id"),
        pa.chunked_array(
            [[None, *dataset.ids["canonical_object_id"].to_pylist()[1:]]],
            type=pa.string(),
        ),
    )
    provenance = dataset.provenance.set_column(
        dataset.provenance.schema.get_field_index("source_name"),
        dataset.provenance.schema.field("source_name"),
        pa.chunked_array(
            [[None, *dataset.provenance["source_name"].to_pylist()[1:]]],
            type=pa.dictionary(pa.int8(), pa.string()),
        ),
    )
    result = StableIdValidator().validate(
        replace(dataset, ids=ids, provenance=provenance),
        regeneration_digest=digest,
    )
    assert not result.valid
    assert result.global_null_id_count == 1
    assert result.provenance_missing_count == 1
