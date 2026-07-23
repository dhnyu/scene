from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from conftest import make_config_data, make_poi_canonical_fixture, write_config


def test_poi_cli_creates_dataset_and_reports(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    schema_path = project / "docs" / "contracts" / "canonical_schema.yaml"
    manifest, _ = make_poi_canonical_fixture(tmp_path, schema_path)
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
            "pois",
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
    assert output["poi_geometry_feature_count"] == 2
    assert output["poi_attribute_row_count"] == 2
    assert output["join_key_validation"] == "PASS"
    assert output["category_path_validation"] == "PASS"
    assert output["failure_count"] == 0
    for key in (
        "geometry_geopackage",
        "attribute_parquet",
        "metadata_json",
        "markdown_report",
        "json_report",
    ):
        assert Path(output[key]).is_file()


def test_poi_help() -> None:
    project = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [sys.executable, "-m", "scene.cli", "pois", "--help"],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout
    assert "--canonical-manifest" in result.stdout
