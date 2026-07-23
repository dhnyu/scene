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


def test_dem_metadata_and_cross_grid_diagnostics(tmp_path: Path) -> None:
    landcover, dem = _sources(tmp_path)
    result = RasterValidator().validate(landcover, dem)
    assert result.dem.valid
    assert result.grid_alignment.same_crs
    assert not result.grid_alignment.same_resolution
    assert not result.grid_alignment.same_origin
    assert not result.grid_alignment.same_extent
    assert not result.grid_alignment.same_grid
    assert not result.resampling_policy_selected


def test_dem_affine_extent_mismatch(tmp_path: Path) -> None:
    landcover, dem = _sources(tmp_path)
    assert dem.extent is not None
    invalid = replace(
        dem,
        extent=(dem.extent[0], dem.extent[1], dem.extent[2] + 1, dem.extent[3]),
    )
    result = RasterValidator().validate(landcover, invalid)
    assert not result.dem.geometry_alignment_valid
    assert not result.valid
