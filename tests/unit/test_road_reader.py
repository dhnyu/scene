from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_road_canonical_fixture
from scene.roads.exceptions import RoadReaderError
from scene.roads.reader import RoadReader


def test_road_reader_reads_only_link_and_node(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_road_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    road_input = RoadReader(schema, tmp_path / "outputs").read(manifest)
    assert road_input.link_table.num_rows == 1
    assert road_input.node_table.num_rows == 1
    assert road_input.link_source.source_name == "seoul_roads_links"
    assert road_input.node_source.source_name == "seoul_roads_nodes"


def test_road_reader_rejects_hash_mismatch(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_road_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["frames"][0]["output_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RoadReaderError, match="SHA-256 mismatch"):
        RoadReader(schema, tmp_path / "outputs").read(manifest)
