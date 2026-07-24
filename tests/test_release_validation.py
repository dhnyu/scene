from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scene.cli import build_parser
from scene.core.config import load_config
from scene.release_validation.artifacts import resolve_reference_artifacts
from scene.release_validation.audit import (
    boundary_content,
    canonical_content,
    id_audit,
    inventory_content,
    manifest_audit,
    miniature_content,
    open_decisions,
    provenance_audit,
    repository_audit,
    scene_content,
    schema_audit,
    split_content,
    storage_audit,
)
from scene.release_validation.serialization import write_release_artifacts


def test_reference_artifact_chain_resolves(
    project_config_path: Path,
) -> None:
    config = load_config(project_config_path)
    artifacts = resolve_reference_artifacts(config)
    assert artifacts.inventory_json.is_file()
    assert artifacts.canonical_manifest.is_file()
    assert (
        artifacts.miniature_directory / "validation.json"
    ).is_file()


def test_reference_manifests_and_provenance_are_internally_valid(
    project_config_path: Path,
) -> None:
    config = load_config(project_config_path)
    artifacts = resolve_reference_artifacts(config)
    assert manifest_audit(config, artifacts)["valid"] is True
    assert provenance_audit(config, artifacts)["valid"] is True
    assert id_audit(artifacts)["valid"] is True


def test_schema_audit_detects_reference_contract_drift(
    project_config_path: Path,
) -> None:
    config = load_config(project_config_path)
    artifacts = resolve_reference_artifacts(config)
    audit = schema_audit(config, artifacts, artifacts)
    assert audit["reference_schema_match"] is False
    assert audit["valid"] is False


def test_stage_content_readers(project_config_path: Path) -> None:
    config = load_config(project_config_path)
    artifacts = resolve_reference_artifacts(config)
    assert inventory_content(artifacts.inventory_json)["row_count"] == 11
    assert canonical_content(artifacts.canonical_manifest)["frame_count"] == 11
    assert id_audit(artifacts)["entity_counts"] == {
        "building": 723875,
        "poi": 498828,
        "road_link": 66854,
        "road_node": 48748,
    }
    assert boundary_content(artifacts.boundary_directory)["row_count"] == 25
    assert split_content(artifacts.split_directory)["row_count"] == 25
    assert scene_content(artifacts.scene_directory)["row_count"] == 6916
    assert miniature_content(artifacts.miniature_directory)["row_count"] == 9


def test_inventory_content_ignores_run_metadata(tmp_path: Path) -> None:
    base = {
        "records": [
            {
                "config_hash": "a",
                "run_id": "run-a",
                "scan_duration_seconds": 1.0,
                "scanned_at_kst": "time-a",
                "source_name": "source",
                "sha256": "b" * 64,
            }
        ]
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(base), encoding="utf-8")
    changed = json.loads(json.dumps(base))
    changed["records"][0].update(
        {
            "config_hash": "z",
            "run_id": "run-b",
            "scan_duration_seconds": 2.0,
            "scanned_at_kst": "time-b",
        }
    )
    second.write_text(json.dumps(changed), encoding="utf-8")
    assert (
        inventory_content(first)["content_hash"]
        == inventory_content(second)["content_hash"]
    )


def test_storage_and_repository_audit(project_config_path: Path) -> None:
    config = load_config(project_config_path)
    artifacts = resolve_reference_artifacts(config)
    assert storage_audit(
        artifacts,
        artifacts,
        config.paths.output_root,
    )["valid"] is True
    repository = repository_audit(config.paths.project_root)
    assert repository["valid"] is True
    assert repository["broken_markdown_link_count"] == 0


def test_observation_blocking_open_decisions(project_root: Path) -> None:
    audit = open_decisions(
        project_root / "docs" / "decisions" / "decision_log.md"
    )
    assert {
        row["decision_id"] for row in audit["m2_1_blocking"]
    } == {"D-004", "D-006", "D-012"}


def test_release_serialization(tmp_path: Path) -> None:
    payloads = {
        key: {"status": "PASS"}
        for key in (
            "pipeline_replay",
            "hash_comparison",
            "geometry_audit",
            "id_audit",
            "manifest_audit",
            "provenance_audit",
            "repository_audit",
            "performance",
            "release_summary",
        )
    }
    artifacts = write_release_artifacts(tmp_path, payloads)
    for path in artifacts.to_dict().values():
        assert json.loads(Path(path).read_text()) == {"status": "PASS"}


def test_release_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["release", "validate", "--help"])
    assert exit_info.value.code == 0
    assert "--config" in capsys.readouterr().out


def test_parquet_codec_fixture_is_zstd(tmp_path: Path) -> None:
    path = tmp_path / "fixture.parquet"
    pq.write_table(pa.table({"value": [1]}), path, compression="zstd")
    metadata = pq.ParquetFile(path).metadata
    assert metadata.row_group(0).column(0).compression == "ZSTD"
