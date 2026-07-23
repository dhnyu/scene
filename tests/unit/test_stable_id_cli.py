from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from conftest import make_stable_id_canonical_fixture


def test_stable_id_cli_builds_registry(
    tmp_path: Path,
    canonical_schema_path: Path,
) -> None:
    project = Path(__file__).resolve().parents[2]
    config, manifest = make_stable_id_canonical_fixture(
        tmp_path,
        canonical_schema_path,
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "ids",
            "build",
            "--config",
            str(config),
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
    assert output["validation"] == "PASS"
    assert output["failure_count"] == 0
    assert output["building_id_count"] == 2
    assert output["road_link_id_count"] == 1
    assert output["road_node_id_count"] == 1
    assert output["poi_id_count"] == 2


def test_stable_id_build_help() -> None:
    project = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [sys.executable, "-m", "scene.cli", "ids", "build", "--help"],
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
