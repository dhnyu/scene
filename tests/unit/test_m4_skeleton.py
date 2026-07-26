from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scene.cli import main
from scene.m4.schemas import M4_APPROVED_DECISIONS, M4_STAGE_IDS
from scene.m4.workflow import load_m4_skeleton_config, run_m4_stage


def test_m4_skeleton_config_declares_explicit_stage_sequence() -> None:
    config = load_m4_skeleton_config(Path("configs/m4/m4_skeleton.yaml"))
    m4_config = config["m4"]

    assert tuple(m4_config["approved_decisions"]) == M4_APPROVED_DECISIONS
    assert tuple(m4_config["stages"].keys()) == M4_STAGE_IDS
    assert m4_config["stage_policy"] == "explicit_stage_only"
    assert m4_config["stages"]["M4.1"]["implementation_status"] == "skeleton_ready"
    assert m4_config["stages"]["M4.2"]["implementation_status"] == "relative_ready"
    assert m4_config["stages"]["M4.3"]["implementation_status"] == "primitive_ready"
    assert m4_config["stages"]["M4.3A"]["implementation_status"] == "backend_validation_ready"
    for stage_id in M4_STAGE_IDS[4:]:
        assert m4_config["stages"][stage_id]["implementation_status"] == "not_started"


def test_m4_stage_runner_writes_only_skeleton_metadata(tmp_path: Path) -> None:
    result = run_m4_stage(
        Path("configs/m4/m4_skeleton.yaml"),
        stage_id="M4.1",
        output_dir=tmp_path,
    )

    stage_dir = Path(str(result["stage_dir"]))
    manifest = json.loads((stage_dir / "m4_1_stage_manifest.json").read_text())
    acceptance = json.loads((stage_dir / "m4_1_acceptance_result.json").read_text())
    audit = json.loads((stage_dir / "m4_1_audit_result.json").read_text())

    assert result["status"] == "PASS"
    assert manifest["stage_id"] == "M4.1"
    assert manifest["auto_continue"] is False
    assert manifest["next_stage"] == "M4.2"
    assert acceptance["status"] == "PASS"
    assert audit["m4_2_plus_started"] is False
    assert (stage_dir / "M4_1_PASS").is_file()
    assert not any(stage_dir.glob("*.pt"))
    assert not any(stage_dir.glob("*.parquet"))


def test_m4_stage_runner_rejects_m4_2_without_m4_1_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires explicit --m4-1-dir"):
        run_m4_stage(
            Path("configs/m4/m4_skeleton.yaml"),
            stage_id="M4.2",
            output_dir=tmp_path,
        )


def test_m4_cli_help_and_explicit_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["m4", "run-stage", "--help"])
    assert exit_info.value.code == 0
    assert "--stage" in capsys.readouterr().out

    exit_code = main(
        [
            "m4",
            "run-stage",
            "--stage",
            "M4.1",
            "--config",
            "configs/m4/m4_skeleton.yaml",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0


def test_m4_schema_files_are_valid_json_or_yaml() -> None:
    for path in (
        Path("configs/m4/m4_stage_manifest.schema.json"),
        Path("configs/m4/m4_stage_report.schema.json"),
        Path("configs/m4/m4_stage_checkpoint.schema.json"),
    ):
        assert json.loads(path.read_text(encoding="utf-8"))["schema_id"].startswith(
            "scene.m4."
        )
    assert yaml.safe_load(Path("configs/m4/m4_skeleton.yaml").read_text())["m4"][
        "milestone"
    ] == "M4"
