from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio

from conftest import make_building_canonical_fixture
from scene.buildings.adapter import BuildingAdapter
from scene.buildings.reader import BuildingReader
from scene.buildings.serialization import BuildingSerializer
from scene.buildings.validator import BuildingValidator


def test_building_dataset_serialization(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    manifest, schema = make_building_canonical_fixture(
        tmp_path,
        project / "docs" / "contracts" / "canonical_schema.yaml",
    )
    canonical = BuildingReader(schema, tmp_path / "outputs").read(manifest)
    result = BuildingAdapter(BuildingValidator(schema)).adapt(canonical)

    artifacts = BuildingSerializer().serialize(
        result.dataset,
        result.validation,
        tmp_path / "serialized",
        run_id="20260724_020000_KST",
    )

    vector = pyogrio.read_info(
        artifacts.geometry_geopackage,
        layer="buildings",
    )
    assert vector["features"] == 1
    assert vector["crs"] == "EPSG:5186"
    assert vector["geometry_type"] == "MultiPolygon"
    parquet = pq.ParquetFile(artifacts.attribute_parquet)
    assert parquet.metadata.num_rows == 1
    assert {
        parquet.metadata.row_group(row_group).column(column).compression
        for row_group in range(parquet.metadata.num_row_groups)
        for column in range(parquet.metadata.num_columns)
    } == {"ZSTD"}
    metadata = json.loads(
        artifacts.metadata_json.read_text(encoding="utf-8")
    )
    assert metadata["modalities_joined"] is False
    assert metadata["stable_id_created"] is False
    assert metadata["observed_area_created"] is False
    assert metadata["validation"]["valid"] is True
