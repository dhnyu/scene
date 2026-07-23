from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_building_canonical_fixture
from scene.buildings.exceptions import BuildingReaderError
from scene.buildings.reader import BuildingReader


def test_building_reader_reads_only_two_building_frames(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    manifest, schema = make_building_canonical_fixture(
        tmp_path,
        project / "docs" / "contracts" / "canonical_schema.yaml",
    )

    result = BuildingReader(schema, tmp_path / "outputs").read(manifest)

    assert result.geometry_table.num_rows == 1
    assert result.attribute_table.num_rows == 1
    assert result.geometry_source.source_name == "seoul_buildings_geometry"
    assert result.attribute_source.source_name == "seoul_buildings_attributes"
    assert result.geometry_crs == "EPSG:5186"
    assert result.geometry_type == "MultiPolygon"
    assert not (manifest.parent / "must_not_be_read.parquet").exists()


def test_building_reader_rejects_canonical_hash_mismatch(
    tmp_path: Path,
) -> None:
    project = Path(__file__).resolve().parents[2]
    manifest, schema = make_building_canonical_fixture(
        tmp_path,
        project / "docs" / "contracts" / "canonical_schema.yaml",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["frames"][0]["output_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BuildingReaderError, match="SHA-256 mismatch"):
        BuildingReader(schema, tmp_path / "outputs").read(manifest)
