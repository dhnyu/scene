from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pyarrow.parquet as pq

from conftest import make_config_data, write_config


def test_canonical_cli_creates_parquet_json_and_reports(tmp_path: Path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "external").mkdir()
    source = tmp_path / "inputs" / "landcover.tif"
    source.write_bytes(b"read-only raster fixture")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[2]
    data = make_config_data(tmp_path)
    data["paths"]["canonical_schema"] = str(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    )
    data["sources"] = [
        {
            "source_name": "seoul_landcover",
            "category": "landcover",
            "kind": "raster",
            "path": "landcover.tif",
        }
    ]
    config_path = write_config(tmp_path / "project.yaml", data)
    inventory_dir = tmp_path / "metadata" / "inventory"
    inventory_dir.mkdir(parents=True)
    inventory_path = inventory_dir / "20260724_000000_KST_source_inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_name": "seoul_landcover",
                        "source_path": str(source),
                        "source_kind": "raster",
                        "valid": True,
                        "sha256": source_hash,
                        "crs": "EPSG:5186",
                        "raster_width": 4,
                        "raster_height": 3,
                        "resolution_x": 5.0,
                        "resolution_y": 5.0,
                        "extent_min_x": 100.0,
                        "extent_min_y": 200.0,
                        "extent_max_x": 120.0,
                        "extent_max_y": 215.0,
                        "band_count": 1,
                        "dtype": "Byte",
                        "nodata": "0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "canonical",
            "--config",
            str(config_path),
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "complete"
    assert output["mapped_source_count"] == 1
    assert output["schema_validation"] == "PASS"
    assert Path(output["canonical_manifest_json"]).is_file()
    assert Path(output["markdown_report"]).is_file()
    assert Path(output["json_report"]).is_file()
    parquet = next(Path(output["output_directory"]).glob("*.parquet"))
    assert pq.read_table(parquet).num_rows == 1
