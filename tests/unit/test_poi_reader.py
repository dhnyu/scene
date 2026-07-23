from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_poi_canonical_fixture
from scene.pois.exceptions import POIReaderError
from scene.pois.reader import POIReader


def test_poi_reader_reads_only_poi_frames(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_poi_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    poi_input = POIReader(schema, tmp_path / "outputs").read(manifest)
    assert poi_input.geometry_table.num_rows == 2
    assert poi_input.attribute_table.num_rows == 2
    assert poi_input.geometry_source.source_name == "seoul_poi_geometry"
    assert poi_input.attribute_source.source_name == "seoul_poi_attributes"


def test_poi_reader_rejects_hash_mismatch(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_poi_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["frames"][0]["output_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(POIReaderError, match="SHA-256 mismatch"):
        POIReader(schema, tmp_path / "outputs").read(manifest)
