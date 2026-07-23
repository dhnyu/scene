from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from conftest import make_raster_config_fixture
from scene.core.config import load_config
from scene.raster.reader import RasterReader
from scene.raster.validator import RasterValidator


def _sources(tmp_path: Path):
    config = load_config(make_raster_config_fixture(tmp_path))
    return RasterReader().read(config)


def test_landcover_metadata_validation(tmp_path: Path) -> None:
    landcover, dem = _sources(tmp_path)
    result = RasterValidator().validate(landcover, dem)
    assert result.valid
    assert result.landcover.crs_valid
    assert result.landcover.band_count_valid
    assert result.landcover.nodata_valid
    assert result.landcover.geometry_alignment_valid
    assert result.landcover.north_up


def test_landcover_invalid_crs_band_and_nodata(tmp_path: Path) -> None:
    landcover, dem = _sources(tmp_path)
    invalid = replace(
        landcover,
        crs="EPSG:4326",
        band_count=2,
        nodata=None,
    )
    result = RasterValidator().validate(invalid, dem)
    assert not result.valid
    assert not result.landcover.crs_valid
    assert not result.landcover.band_count_valid
    assert not result.landcover.nodata_valid
