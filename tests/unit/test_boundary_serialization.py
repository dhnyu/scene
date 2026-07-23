from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio

from scene.boundaries.adapter import adapt_seoul_districts
from scene.boundaries.reader import audit_boundary_source, read_seoul_features
from scene.boundaries.serialization import write_boundary_artifacts
from scene.boundaries.spatial_audit import audit_spatial_consistency
from scene.boundaries.validator import validate_canonical_districts
from test_boundary_adapter import _write_source


def test_boundary_serialization_is_canonical_and_zstd(tmp_path: Path) -> None:
    audit = audit_boundary_source(_write_source(tmp_path / "official.gpkg"))
    source_districts, source_seoul = read_seoul_features(audit)
    dataset = adapt_seoul_districts(
        source_districts,
        source_seoul,
        audit,
        source_name="official_sigungu",
    )
    validation = validate_canonical_districts(dataset)
    spatial = audit_spatial_consistency(dataset.districts, dataset.seoul)

    artifacts = write_boundary_artifacts(
        dataset,
        validation,
        spatial,
        tmp_path / "output",
        run_id="20260724_120000_KST",
        config_hash="a" * 64,
        canonical_schema_version="1.1.0",
    )

    assert {str(item[0]) for item in pyogrio.list_layers(artifacts.geopackage)} == {
        "seoul_sido",
        "seoul_sigungu",
    }
    assert (
        pq.ParquetFile(artifacts.districts_parquet)
        .metadata.row_group(0)
        .column(0)
        .compression
        == "ZSTD"
    )
    assert pq.read_table(artifacts.provenance_parquet).num_rows == 25
    assert json.loads(artifacts.validation_json.read_text())["valid"] is True
