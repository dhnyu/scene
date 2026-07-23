from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio

from conftest import make_poi_canonical_fixture
from scene.pois.adapter import POIAdapter
from scene.pois.reader import POIReader
from scene.pois.serialization import POISerializer
from scene.pois.validator import POIValidator


def test_poi_dataset_serialization(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    manifest, schema = make_poi_canonical_fixture(
        tmp_path, canonical_schema_path
    )
    canonical = POIReader(schema, tmp_path / "outputs").read(manifest)
    result = POIAdapter(POIValidator(schema)).adapt(canonical)
    artifacts = POISerializer().serialize(
        result.dataset,
        result.validation,
        tmp_path / "serialized",
        run_id="20260724_040000_KST",
    )

    info = pyogrio.read_info(artifacts.geometry_geopackage, layer="pois")
    assert (info["features"], info["geometry_type"], info["crs"]) == (
        2,
        "Point",
        "EPSG:5186",
    )
    parquet = pq.ParquetFile(artifacts.attribute_parquet)
    assert parquet.metadata.num_rows == 2
    assert {
        parquet.metadata.row_group(row_group).column(column).compression
        for row_group in range(parquet.metadata.num_row_groups)
        for column in range(parquet.metadata.num_columns)
    } == {"ZSTD"}
    assert "poi_category_path" in parquet.schema_arrow.names

    metadata = json.loads(artifacts.metadata_json.read_text(encoding="utf-8"))
    assert metadata["geometry_attributes_joined"] is False
    assert metadata["geometry_modality_created"] is False
    assert metadata["records_deduplicated"] is False
    assert metadata["records_merged"] is False
    assert metadata["stable_id_created"] is False
    assert metadata["validation"]["valid"] is True
    assert metadata["validation"]["join_key"]["valid"] is True
    assert metadata["category_path_contract"]["normalization"] == "identity"
    assert metadata["category_path_contract"]["separator"] == " > "
