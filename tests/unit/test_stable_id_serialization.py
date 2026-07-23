from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from conftest import make_stable_id_canonical_fixture
from scene.id.generator import StableIdGenerator
from scene.id.reader import StableIdReader
from scene.id.serialization import StableIdSerializer
from scene.id.validator import StableIdValidator
from scene.schema.schema import load_canonical_schema


def test_stable_id_serialization_is_zstandard(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    _, manifest = make_stable_id_canonical_fixture(
        tmp_path,
        canonical_schema_path,
    )
    source = StableIdReader(
        load_canonical_schema(canonical_schema_path),
        tmp_path / "outputs",
    ).read(manifest)
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
    validation = StableIdValidator().validate(
        dataset,
        regeneration_digest=digest,
    )
    output = tmp_path / "serialized"
    artifacts = StableIdSerializer().serialize(
        dataset,
        validation,
        output,
        run_id="20260724_070000_KST",
        config_hash="f" * 64,
    )
    assert {path.name for path in output.iterdir()} == {
        "ids.parquet",
        "provenance.parquet",
        "ids.json",
    }
    payload = json.loads(artifacts.ids_json.read_text(encoding="utf-8"))
    assert payload["validation"]["valid"]
    assert payload["scene_based_ids_materialized"] is False
    for path in (artifacts.ids_parquet, artifacts.provenance_parquet):
        parquet = pq.ParquetFile(path)
        assert parquet.metadata.num_rows == 6
        assert {
            parquet.metadata.row_group(group).column(column).compression
            for group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.num_columns)
        } == {"ZSTD"}
