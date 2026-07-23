from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from conftest import make_config_data, make_road_canonical_fixture, write_config


def test_road_cli_creates_datasets_and_reports(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    schema_path = project / "docs" / "contracts" / "canonical_schema.yaml"
    manifest, _ = make_road_canonical_fixture(tmp_path, schema_path)
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
            "roads",
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
    assert output["road_link_feature_count"] == 1
    assert output["road_node_feature_count"] == 1
    assert output["validation"] == "PASS"
    assert output["failure_count"] == 0
    for key in (
        "geometry_geopackage",
        "link_attribute_parquet",
        "node_attribute_parquet",
        "metadata_json",
        "markdown_report",
        "json_report",
    ):
        assert Path(output[key]).is_file()


def test_cli_help_lists_roads() -> None:
    project = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [sys.executable, "-m", "scene.cli", "--help"],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "roads" in result.stdout
