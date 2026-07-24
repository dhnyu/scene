from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from conftest import make_config_data, write_config


def test_observation_contract_cli_creates_only_reports(
    project_root: Path,
    tmp_path: Path,
) -> None:
    contract_dir = tmp_path / "docs" / "contracts"
    fixture_dir = tmp_path / "tests" / "fixtures" / "observations"
    contract_dir.mkdir(parents=True)
    fixture_dir.mkdir(parents=True)
    schema = contract_dir / "scene_observation_schema.yaml"
    contract = contract_dir / "scene_observation_contract.md"
    fixture = fixture_dir / "m2_1_scene_observation_fixture.yaml"
    shutil.copyfile(
        project_root / "docs" / "contracts" / schema.name,
        schema,
    )
    shutil.copyfile(
        project_root / "docs" / "contracts" / contract.name,
        contract,
    )
    shutil.copyfile(
        project_root / "tests" / "fixtures" / "observations" / fixture.name,
        fixture,
    )
    config = make_config_data(tmp_path)
    config["paths"]["canonical_schema"] = str(
        project_root / "docs" / "contracts" / "canonical_schema.yaml"
    )
    config_path = write_config(tmp_path / "project.yaml", config)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "observations",
            "validate-contract",
            "--config",
            str(config_path),
            "--schema",
            str(schema),
            "--fixture",
            str(fixture),
        ],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "complete"
    assert output["schema_validation"] == "PASS"
    assert output["fixture_validation"] == "PASS"
    assert output["source_access"] is False
    assert output["forbidden_artifact_count"] == 0
    assert output["m2_2_road_materialization"] == "BLOCKED"
    assert Path(output["markdown_report"]).is_file()
    assert Path(output["json_report"]).is_file()
    assert not (tmp_path / "outputs" / "observations").exists()


def test_observation_cli_help(project_root: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scene.cli",
            "observations",
            "validate-contract",
            "--help",
        ],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "--schema" in result.stdout
    assert "--fixture" in result.stdout
