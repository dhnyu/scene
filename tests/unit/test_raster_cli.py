from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from conftest import make_raster_config_fixture


def test_raster_cli_builds_metadata_and_reports(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    config = make_raster_config_fixture(tmp_path)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "raster",
            "build",
            "--config",
            str(config),
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
    assert output["validation"] == "PASS"
    assert output["source_count"] == 2
    assert output["failure_count"] == 0
    assert output["grid_alignment"]["same_grid"] is False
    for key in (
        "metadata_json",
        "metadata_parquet",
        "markdown_report",
        "json_report",
    ):
        assert Path(output[key]).is_file()


def test_raster_build_help() -> None:
    project = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [sys.executable, "-m", "scene.cli", "raster", "build", "--help"],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--config" in result.stdout
