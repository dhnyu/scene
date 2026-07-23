from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from conftest import make_raster_config_fixture
from scene.core.config import load_config
from scene.raster.metadata import RasterMetadataCollection
from scene.raster.reader import RasterReader
from scene.raster.serialize import RasterSerializer
from scene.raster.validator import RasterValidator


def test_raster_metadata_serialization(tmp_path: Path) -> None:
    config = load_config(make_raster_config_fixture(tmp_path))
    landcover, dem = RasterReader().read(config)
    validation = RasterValidator().validate(landcover, dem)
    collection = RasterMetadataCollection(
        landcover=landcover,
        dem=dem,
        grid_alignment=validation.grid_alignment,
    )
    output = tmp_path / "serialized"
    artifacts = RasterSerializer().serialize(
        collection,
        validation,
        output,
        run_id="20260724_050000_KST",
    )
    assert {path.name for path in output.iterdir()} == {
        "raster_metadata.json",
        "raster_metadata.parquet",
    }
    payload = json.loads(artifacts.metadata_json.read_text(encoding="utf-8"))
    assert payload["pixel_artifacts_created"] is False
    assert payload["source_raster_copies_created"] is False
    assert payload["validation"]["valid"] is True
    parquet = pq.ParquetFile(artifacts.metadata_parquet)
    assert parquet.metadata.num_rows == 2
    assert {
        parquet.metadata.row_group(row_group).column(column).compression
        for row_group in range(parquet.metadata.num_row_groups)
        for column in range(parquet.metadata.num_columns)
    } == {"ZSTD"}
