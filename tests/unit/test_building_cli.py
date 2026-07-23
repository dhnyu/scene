from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from conftest import (
    make_building_canonical_fixture,
    make_config_data,
    write_config,
)


def test_building_cli_creates_dataset_and_reports(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    schema_path = project / "docs" / "contracts" / "canonical_schema.yaml"
    manifest, _ = make_building_canonical_fixture(tmp_path, schema_path)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "external").mkdir()
    config = make_config_data(tmp_path)
    config["paths"]["canonical_schema"] = str(schema_path)
    config_path = write_config(tmp_path / "project.yaml", config)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "buildings",
            "--config",
            str(config_path),
            "--canonical-manifest",
            str(manifest),
        ],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "complete"
    assert output["building_feature_count"] == 1
    assert output["validation"] == "PASS"
    assert output["failure_count"] == 0
    assert Path(output["geometry_geopackage"]).is_file()
    assert Path(output["attribute_parquet"]).is_file()
    assert Path(output["building_metadata_json"]).is_file()
    assert Path(output["markdown_report"]).is_file()
    assert Path(output["json_report"]).is_file()
