from __future__ import annotations

import json
from pathlib import Path
import subprocess

from scene.inventory.vector import extract_vector_metadata


def test_vector_metadata_from_temporary_geopackage(tmp_path: Path) -> None:
    geojson = tmp_path / "points.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "fixture"},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [200000.0, 500000.0],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    geopackage = tmp_path / "points.gpkg"
    result = subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            str(geopackage),
            str(geojson),
            "-nln",
            "fixture_points",
            "-a_srs",
            "EPSG:5186",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    metadata = extract_vector_metadata(geopackage, "fixture_points")

    assert metadata.crs == "EPSG:5186"
    assert metadata.geometry_type == "Point"
    assert metadata.feature_count == 1
    assert metadata.bbox == (200000.0, 500000.0, 200000.0, 500000.0)
    assert metadata.layer_name == "fixture_points"
