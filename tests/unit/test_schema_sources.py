from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pyarrow.parquet as pq

from scene.core.config import SourceConfig
from scene.schema.schema import load_canonical_schema
from scene.schema.sources import map_source


def test_vector_source_crs_geometry_and_mapping(tmp_path: Path) -> None:
    geojson = tmp_path / "nodes.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "NODE_ID": "n-1",
                            "NODE_TYPE": "type",
                            "NODE_NAME": "name",
                            "TURN_P": "none",
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [200000.0, 550000.0],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    geopackage = tmp_path / "nodes.gpkg"
    conversion = subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            str(geopackage),
            str(geojson),
            "-nln",
            "nodes",
            "-a_srs",
            "EPSG:5186",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert conversion.returncode == 0, conversion.stderr
    source = SourceConfig(
        source_name="seoul_roads_nodes",
        category="roads",
        kind="vector",
        path=geopackage,
        layer="nodes",
    )
    root = Path(__file__).resolve().parents[2]
    schema = load_canonical_schema(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    )
    inventory = {
        "source_name": source.source_name,
        "source_path": str(geopackage),
        "sha256": hashlib.sha256(geopackage.read_bytes()).hexdigest(),
        "crs": "EPSG:5186",
        "geometry_type": "Point",
    }
    output = tmp_path / "canonical.parquet"

    result = map_source(
        source,
        schema.frame_for(source.source_name),
        inventory,
        output,
        schema_version=schema.schema_version,
    )

    assert result.valid
    assert result.row_count == 1
    assert result.crs == "EPSG:5186"
    assert result.geometry_type == "Point"
    assert pq.read_table(output).to_pylist()[0]["source_node_id"] == "n-1"
