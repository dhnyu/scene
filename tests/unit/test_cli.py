from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import make_config_data, write_config
from scene.cli import main
from scene.cli import run_foundation


def test_cli_help_exit_code_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert "inventory" in capsys.readouterr().out


def test_module_cli_help_exit_code_zero() -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [sys.executable, "-m", "scene.cli", "--help"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "inventory" in result.stdout


def test_scene_footprint_cli_help_exit_code_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["scenes", "generate-footprints", "--help"])

    assert exit_info.value.code == 0
    assert "M1.7 fixed 500 m scene footprints" in capsys.readouterr().out


def test_foundation_cli_run_uses_only_configured_outputs(tmp_path: Path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "external").mkdir()
    config_path = write_config(
        tmp_path / "project.yaml",
        make_config_data(tmp_path),
    )

    result = run_foundation(config_path, "INFO")

    assert result["status"] == "complete"
    assert Path(result["resolved_config"]).is_file()
    assert Path(result["markdown_report"]).is_file()
    assert Path(result["json_report"]).is_file()
