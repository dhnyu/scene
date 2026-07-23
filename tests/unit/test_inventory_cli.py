from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pyarrow.parquet as pq

from conftest import make_config_data, write_config


def test_inventory_cli_creates_all_artifacts(tmp_path: Path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "external").mkdir()
    source = tmp_path / "inputs" / "attributes.parquet"
    source.write_bytes(b"read-only fixture")
    data = make_config_data(tmp_path)
    data["sources"] = [
        {
            "source_name": "building_attributes",
            "category": "buildings",
            "kind": "tabular",
            "path": "attributes.parquet",
        }
    ]
    config_path = write_config(tmp_path / "project.yaml", data)
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "inventory",
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
    assert output["source_count"] == 1
    assert output["failure_count"] == 0
    assert output["source_stat_changes"] == []
    assert Path(output["inventory_json"]).is_file()
    assert Path(output["inventory_parquet"]).is_file()
    assert Path(output["markdown_report"]).is_file()
    assert Path(output["json_report"]).is_file()
    assert pq.read_table(output["inventory_parquet"]).num_rows == 1
