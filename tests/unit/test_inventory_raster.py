from __future__ import annotations

from pathlib import Path
import subprocess

from scene.inventory.raster import extract_raster_metadata


def test_raster_metadata_from_temporary_geotiff(tmp_path: Path) -> None:
    raster = tmp_path / "fixture.tif"
    result = subprocess.run(
        [
            "gdal_create",
            "-of",
            "GTiff",
            "-outsize",
            "4",
            "3",
            "-bands",
            "1",
            "-burn",
            "7",
            "-ot",
            "Float32",
            "-a_srs",
            "EPSG:5186",
            "-a_ullr",
            "100",
            "230",
            "140",
            "200",
            "-a_nodata",
            "-9999",
            str(raster),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    metadata = extract_raster_metadata(raster)

    assert metadata.crs == "EPSG:5186"
    assert metadata.width == 4
    assert metadata.height == 3
    assert metadata.resolution == (10.0, 10.0)
    assert metadata.extent == (100.0, 200.0, 140.0, 230.0)
    assert metadata.band_count == 1
    assert metadata.dtype == "Float32"
    assert metadata.nodata == "-9999.0"
