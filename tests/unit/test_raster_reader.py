from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_raster_config_fixture
from scene.core.config import load_config
from scene.raster.exceptions import RasterReaderError
from scene.raster.reader import RasterReader


def test_raster_reader_extracts_header_and_provenance(tmp_path: Path) -> None:
    config = load_config(make_raster_config_fixture(tmp_path))
    landcover, dem = RasterReader().read(config)
    assert landcover.source_name == "seoul_landcover"
    assert dem.source_name == "seoul_dem"
    assert landcover.crs == dem.crs == "EPSG:5186"
    assert landcover.resolution == (5.0, 5.0)
    assert dem.resolution == (30.0, 30.0)
    assert landcover.nodata == "0"
    assert dem.nodata == "-32767.0"
    assert landcover.compression == dem.compression == "DEFLATE"
    assert len(landcover.sha256) == len(dem.sha256) == 64
    assert landcover.pixel_data_read is False
    assert dem.pixel_data_copied is False


def test_raster_reader_rejects_missing_source(tmp_path: Path) -> None:
    config = load_config(make_raster_config_fixture(tmp_path))
    config.sources[0].path.unlink()
    with pytest.raises(RasterReaderError, match="does not exist"):
        RasterReader().read(config)


def test_raster_reader_rejects_invalid_raster(tmp_path: Path) -> None:
    config = load_config(make_raster_config_fixture(tmp_path))
    config.sources[0].path.write_text("not a raster", encoding="ascii")
    with pytest.raises(RasterReaderError, match="cannot read raster metadata"):
        RasterReader().read(config)
