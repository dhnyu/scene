"""M4 explicit stage runner skeleton."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml
import torch

from scene.m4.acceptance import skeleton_acceptance_checks
from scene.m4.acceptance import relative_acceptance_checks
from scene.m4.acceptance import geometry_primitive_acceptance_checks
from scene.m4.acceptance import triangle_backend_acceptance_checks
from scene.m4.schemas import (
    CHECKPOINT_SCHEMA,
    M4_APPROVED_DECISIONS,
    M4_STAGE_IDS,
    MANIFEST_SCHEMA,
    REPORT_SCHEMA,
)
from scene.m4.relative import generate_relative_wavelengths, relative_architecture_metadata
from scene.m4.geometry_frequency import generate_frequency_grid
from scene.m4.geometry_module import geometry_primitive_metadata
from scene.m4.triangle_validation import StressConfig, run_triangle_backend_stress

KST = ZoneInfo("Asia/Seoul")
M4_1_STAGE_ID = "M4.1"


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def load_m4_skeleton_config(path: Path) -> dict[str, object]:
    """Load the M4 skeleton config without mutating project config contracts."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("M4 skeleton config must be a mapping")
    m4_config = payload.get("m4")
    if not isinstance(m4_config, dict):
        raise ValueError("M4 skeleton config requires top-level 'm4'")
    if tuple(m4_config.get("approved_decisions", ())) != M4_APPROVED_DECISIONS:
        raise ValueError("M4 skeleton config decisions must match D-001/D-002/D-003/D-012/D-013")
    stages = m4_config.get("stages")
    if not isinstance(stages, dict) or tuple(stages.keys()) != M4_STAGE_IDS:
        raise ValueError("M4 skeleton config must declare M4.1-M4.6 in order")
    return payload


def run_m4_stage(
    config_path: Path,
    *,
    stage_id: str,
    output_dir: Path | None = None,
    m4_1_dir: Path | None = None,
    m4_2_dir: Path | None = None,
    m4_3_dir: Path | None = None,
    workers: int = 40,
) -> dict[str, object]:
    """Run one explicit M4 stage."""

    if stage_id == M4_1_STAGE_ID:
        return run_m4_1_skeleton(config_path=config_path, output_dir=output_dir)
    if stage_id == "M4.2":
        if m4_1_dir is None:
            raise ValueError("M4.2 requires explicit --m4-1-dir evidence")
        return run_m4_2_relative(
            config_path=config_path,
            output_dir=output_dir,
            m4_1_dir=m4_1_dir,
        )
    if stage_id == "M4.3":
        if m4_2_dir is None:
            raise ValueError("M4.3 requires explicit --m4-2-dir evidence")
        return run_m4_3_geometry_primitive(
            config_path=config_path,
            output_dir=output_dir,
            m4_2_dir=m4_2_dir,
        )
    if stage_id == "M4.3A":
        if m4_3_dir is None:
            raise ValueError("M4.3A requires explicit --m4-3-dir evidence")
        return run_m4_3a_triangle_validation(
            config_path=config_path,
            output_dir=output_dir,
            m4_3_dir=m4_3_dir,
            workers=workers,
        )
    raise ValueError("M4 runner currently supports only explicit M4.1, M4.2, M4.3 or M4.3A")


def run_m4_1_skeleton(
    *,
    config_path: Path,
    output_dir: Path | None = None,
) -> dict[str, object]:
    """Materialize M4.1 skeleton metadata and stop before any encoder work."""

    config = load_m4_skeleton_config(config_path)
    started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    run_id = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S_KST")
    root = output_dir or Path("outputs/m4/skeleton")
    stage_dir = root / run_id / M4_1_STAGE_ID
    stage_dir.mkdir(parents=True, exist_ok=False)

    checks = skeleton_acceptance_checks(config_path=config_path, config=config)
    valid = all(bool(check["passed"]) for check in checks)
    audit_result = {
        "schema_id": "scene.m4.skeleton_audit.v1",
        "stage_id": M4_1_STAGE_ID,
        "audit_status": "PASS" if valid else "FAIL",
        "forbidden_output_status": "not_created",
        "implementation_scope": "skeleton_only",
        "m4_2_plus_started": False,
        "study_methods_modified": False,
        "contracts_modified": False,
        "decisions_modified": False,
    }
    acceptance_result = {
        "schema_id": "scene.m4.acceptance_placeholder.v1",
        "stage_id": M4_1_STAGE_ID,
        "status": "PASS" if valid else "FAIL",
        "checks": checks,
    }
    manifest = {
        "schema_id": MANIFEST_SCHEMA["schema_id"],
        "milestone": "M4",
        "stage_id": M4_1_STAGE_ID,
        "stage_name": "Project Skeleton",
        "stage_status": "PASS" if valid else "FAIL",
        "run_id": run_id,
        "started_at_kst": started_at,
        "config_path": str(config_path),
        "config_hash": _sha256_json(config),
        "approved_decisions": list(M4_APPROVED_DECISIONS),
        "created_files": [
            "m4_1_stage_manifest.json",
            "m4_1_acceptance_result.json",
            "m4_1_audit_result.json",
            "m4_1_stage_report.md",
            "M4_1_PASS",
        ],
        "schemas": {
            "manifest_schema_id": MANIFEST_SCHEMA["schema_id"],
            "report_schema_id": REPORT_SCHEMA["schema_id"],
            "checkpoint_schema_id": CHECKPOINT_SCHEMA["schema_id"],
        },
        "forbidden_outputs": [
            "relative_encoder",
            "geometry_fourier",
            "neural_network",
            "tensor",
            "feature_artifact",
            "model_checkpoint",
        ],
        "next_stage": "M4.2",
        "auto_continue": False,
    }
    report = "\n".join(
        [
            "# M4.1 Project Skeleton Stage Report",
            "",
            "## Created Files",
            "",
            "- `m4_1_stage_manifest.json`",
            "- `m4_1_acceptance_result.json`",
            "- `m4_1_audit_result.json`",
            "- `m4_1_stage_report.md`",
            "- `M4_1_PASS`",
            "",
            "## Scope",
            "",
            "M4.1 created stage-runner metadata only. Relative encoders, geometry "
            "Fourier primitives, neural modules, tensors, feature artifacts and "
            "model checkpoints were not generated.",
            "",
            "## Final Status",
            "",
            "```text",
            "M4.1",
            "PASS" if valid else "FAIL",
            "",
            "M4.2",
            "READY" if valid else "BLOCKED",
            "```",
            "",
        ]
    )

    _write_json(stage_dir / "m4_1_acceptance_result.json", acceptance_result)
    _write_json(stage_dir / "m4_1_audit_result.json", audit_result)
    _write_json(stage_dir / "m4_1_stage_manifest.json", manifest)
    _write_text(stage_dir / "m4_1_stage_report.md", report)
    if valid:
        _write_text(stage_dir / "M4_1_PASS", "PASS\n")

    return {
        "status": "PASS" if valid else "FAIL",
        "stage_id": M4_1_STAGE_ID,
        "run_id": run_id,
        "stage_dir": str(stage_dir),
        "manifest": str(stage_dir / "m4_1_stage_manifest.json"),
        "acceptance_result": str(stage_dir / "m4_1_acceptance_result.json"),
        "audit_result": str(stage_dir / "m4_1_audit_result.json"),
        "pass_marker": str(stage_dir / "M4_1_PASS") if valid else None,
        "auto_continue": False,
    }


def run_m4_3_geometry_primitive(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_2_dir: Path,
) -> dict[str, object]:
    """Run M4.3 geometry primitive acceptance and stage metadata."""

    config = load_m4_skeleton_config(config_path)
    started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    run_id = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S_KST")
    root = output_dir or Path("outputs/m4/geometry_primitive")
    stage_dir = root / run_id / "M4.3"
    stage_dir.mkdir(parents=True, exist_ok=False)

    checks = geometry_primitive_acceptance_checks(m4_2_dir=m4_2_dir)
    failed = [check for check in checks if check["status"] == "FAIL"]
    valid = not failed
    metadata = geometry_primitive_metadata()
    omega_fixture = {
        "schema_id": "scene.m4.geometry_frequency_fixture.v1",
        "stage_id": "M4.3",
        "omega": generate_frequency_grid(dtype=torch.float64).tolist(),
        "ordering": "radius-major, angle-minor",
    }
    acceptance_result = {
        "schema_id": "scene.m4.geometry_primitive_acceptance.v1",
        "stage_id": "M4.3",
        "status": "PASS" if valid else "FAIL",
        "checks": checks,
    }
    audit_result = {
        "schema_id": "scene.m4.geometry_primitive_audit.v1",
        "stage_id": "M4.3",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_2_evidence_dir": str(m4_2_dir),
        "study_methods_modified": False,
        "contracts_modified": False,
        "decisions_modified": False,
        "m3_artifacts_modified": False,
        "m4_4_plus_started": False,
        "frequency_ordering_valid": valid,
        "hole_preserved": valid,
        "multipolygon_preserved": valid,
        "no_repair": valid,
        "finite_output": valid,
        "checkpoint_created": False,
        "production_feature_artifact_created": False,
    }
    primitive_fixture = {
        "schema_id": "scene.m4.geometry_primitive_fixture.v1",
        "stage_id": "M4.3",
        "fixtures": [
            "triangle vertex permutation and zero-frequency area",
            "segment split and reversal invariance",
            "polygon hole subtraction and multipolygon order",
            "polyline multiline and segment order",
            "invalid polygon no-repair failure",
        ],
    }
    manifest = {
        "schema_id": "scene.m4.stage_manifest.v1",
        "milestone": "M4",
        "stage_id": "M4.3",
        "stage_name": "Geometry Fourier Primitive",
        "stage_status": "PASS" if valid else "FAIL",
        "run_id": run_id,
        "started_at_kst": started_at,
        "config_path": str(config_path),
        "config_hash": _sha256_json(config),
        "m4_2_evidence_dir": str(m4_2_dir),
        "approved_decisions": list(M4_APPROVED_DECISIONS),
        "architecture": metadata,
        "created_files": [
            "m4_3_stage_manifest.json",
            "m4_3_acceptance_result.json",
            "m4_3_audit_result.json",
            "m4_3_frequency_fixture.json",
            "m4_3_primitive_fixture.json",
            "m4_3_stage_report.md",
            "M4_3_PASS",
        ],
        "forbidden_outputs": [
            "x_mag",
            "x_phase",
            "e_geom",
            "feature_parquet",
            "checkpoint",
            "trained_model",
            "seoul_full_feature",
            "m4_4_output",
        ],
        "next_stage": "M4.4" if valid else None,
        "auto_continue": False,
    }
    report = "\n".join(
        [
            "# M4.3 Geometry Fourier Primitive Stage Report",
            "",
            "## Scope",
            "",
            "M4.3 implemented and validated geometry Fourier primitives only. "
            "Magnitude, phase, geometry feature, geometry MLP and M4.4+ work "
            "were not started.",
            "",
            "## Final Status",
            "",
            "```text",
            "M4.3",
            "PASS" if valid else "FAIL",
            "",
            "M4.4",
            "READY" if valid else "BLOCKED",
            "",
            "AUTO_CONTINUE",
            "false",
            "```",
            "",
        ]
    )

    _write_json(stage_dir / "m4_3_acceptance_result.json", acceptance_result)
    _write_json(stage_dir / "m4_3_audit_result.json", audit_result)
    _write_json(stage_dir / "m4_3_frequency_fixture.json", omega_fixture)
    _write_json(stage_dir / "m4_3_primitive_fixture.json", primitive_fixture)
    _write_json(stage_dir / "m4_3_stage_manifest.json", manifest)
    _write_text(stage_dir / "m4_3_stage_report.md", report)
    if valid:
        _write_text(stage_dir / "M4_3_PASS", "PASS\n")

    return {
        "status": "PASS" if valid else "FAIL",
        "stage_id": "M4.3",
        "run_id": run_id,
        "stage_dir": str(stage_dir),
        "manifest": str(stage_dir / "m4_3_stage_manifest.json"),
        "acceptance_result": str(stage_dir / "m4_3_acceptance_result.json"),
        "audit_result": str(stage_dir / "m4_3_audit_result.json"),
        "pass_marker": str(stage_dir / "M4_3_PASS") if valid else None,
        "auto_continue": False,
    }


def run_m4_3a_triangle_validation(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_3_dir: Path,
    workers: int = 40,
) -> dict[str, object]:
    """Run M4.3A Triangle backend and real-geometry validation only."""

    config = load_m4_skeleton_config(config_path)
    started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    run_id = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S_KST")
    root = output_dir or Path("outputs/m4/triangle_validation")
    stage_dir = root / run_id / "M4.3A"
    stage_dir.mkdir(parents=True, exist_ok=False)

    stress_result = run_triangle_backend_stress(
        output_dir=stage_dir,
        config=StressConfig(workers=workers),
    )
    checks = triangle_backend_acceptance_checks(
        m4_3_dir=m4_3_dir,
        stress_result=stress_result,
    )
    failed = [check for check in checks if check["status"] == "FAIL"]
    valid = not failed
    acceptance_result = {
        "schema_id": "scene.m4.triangle_backend_acceptance.v1",
        "stage_id": "M4.3A",
        "status": "PASS" if valid else "FAIL",
        "checks": checks,
    }
    audit_result = {
        "schema_id": "scene.m4.triangle_backend_audit.v1",
        "stage_id": "M4.3A",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_3_evidence_dir": str(m4_3_dir),
        "study_methods_modified": False,
        "contracts_modified": False,
        "decisions_modified": True,
        "m3_artifacts_modified": False,
        "geometry_repair_used": False,
        "shapely_fallback_used": False,
        "workers": workers,
        "worker_1_vs_n_exact_parity_required": False,
        "m4_4_plus_started": False,
        "production_feature_artifact_created": False,
        "checkpoint_created": False,
        "auto_continue": False,
    }
    dependency_manifest = {
        "schema_id": "scene.m4.triangle_dependency_manifest.v1",
        "stage_id": "M4.3A",
        "environment": stress_result.get("dependency_environment", {}),
    }
    manifest = {
        "schema_id": "scene.m4.stage_manifest.v1",
        "milestone": "M4",
        "stage_id": "M4.3A",
        "stage_name": "Triangle Backend and Real-Geometry Validation",
        "stage_status": "PASS" if valid else "FAIL",
        "run_id": run_id,
        "started_at_kst": started_at,
        "config_path": str(config_path),
        "config_hash": _sha256_json(config),
        "m4_3_evidence_dir": str(m4_3_dir),
        "approved_decisions": list(M4_APPROVED_DECISIONS),
        "workers": workers,
        "triangle_backend": {
            "function": "triangle.triangulate",
            "options": "pYq",
            "shapely_fallback": False,
        },
        "created_files": [
            "m4_3a_stage_manifest.json",
            "m4_3a_acceptance_result.json",
            "m4_3a_audit_result.json",
            "m4_3a_dependency_manifest.json",
            "triangle_stress_summary.json",
            "triangle_stress_failures.json",
            "m4_3a_stage_report.md",
            "M4_3A_PASS" if valid else "M4_3A_FAIL",
        ],
        "forbidden_outputs": [
            "repaired_geometry",
            "modified_m3_geometry",
            "production_fourier_complex_cache",
            "x_mag",
            "x_phase",
            "e_geom",
            "feature_parquet",
            "model_checkpoint",
            "trained_model",
            "m4_4_output",
            "m5_plus_artifact",
        ],
        "next_stage": "M4.4" if valid else None,
        "auto_continue": False,
    }
    metrics = stress_result.get("metrics", {})
    report = "\n".join(
        [
            "# M4.3A Triangle Backend Stage Report",
            "",
            "## Scope",
            "",
            "M4.3A validates the constrained Triangle polygon backend and real "
            "M3 building observations only. Magnitude, phase, geometry MLP, "
            "feature materialization, checkpoint training and M4.4+ work were "
            "not started.",
            "",
            "## Parallel Policy",
            "",
            "workers=1 and workers=40 bitwise or exact equality is not an "
            "acceptance requirement.",
            "",
            "## Stress Metrics",
            "",
            "```json",
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Final Status",
            "",
            "```text",
            "M4.3A",
            "PASS" if valid else "FAIL",
            "",
            "M4.4",
            "READY" if valid else "BLOCKED",
            "",
            "AUTO_CONTINUE",
            "false",
            "```",
            "",
        ]
    )

    _write_json(stage_dir / "m4_3a_acceptance_result.json", acceptance_result)
    _write_json(stage_dir / "m4_3a_audit_result.json", audit_result)
    _write_json(stage_dir / "m4_3a_dependency_manifest.json", dependency_manifest)
    _write_json(stage_dir / "m4_3a_stage_manifest.json", manifest)
    _write_text(stage_dir / "m4_3a_stage_report.md", report)
    _write_text(stage_dir / ("M4_3A_PASS" if valid else "M4_3A_FAIL"), ("PASS\n" if valid else "FAIL\n"))

    return {
        "status": "PASS" if valid else "FAIL",
        "stage_id": "M4.3A",
        "run_id": run_id,
        "stage_dir": str(stage_dir),
        "manifest": str(stage_dir / "m4_3a_stage_manifest.json"),
        "acceptance_result": str(stage_dir / "m4_3a_acceptance_result.json"),
        "audit_result": str(stage_dir / "m4_3a_audit_result.json"),
        "pass_marker": str(stage_dir / "M4_3A_PASS") if valid else None,
        "auto_continue": False,
    }


def run_m4_2_relative(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_1_dir: Path,
) -> dict[str, object]:
    """Run M4.2 relative-position encoder acceptance and stage metadata."""

    config = load_m4_skeleton_config(config_path)
    started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    run_id = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S_KST")
    root = output_dir or Path("outputs/m4/relative")
    stage_dir = root / run_id / "M4.2"
    stage_dir.mkdir(parents=True, exist_ok=False)

    checks = relative_acceptance_checks(m4_1_dir=m4_1_dir)
    failed = [check for check in checks if check["status"] == "FAIL"]
    valid = not failed
    architecture = relative_architecture_metadata()
    acceptance_result = {
        "schema_id": "scene.m4.relative_acceptance.v1",
        "stage_id": "M4.2",
        "status": "PASS" if valid else "FAIL",
        "checks": checks,
    }
    audit_result = {
        "schema_id": "scene.m4.relative_audit.v1",
        "stage_id": "M4.2",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_1_evidence_dir": str(m4_1_dir),
        "study_methods_modified": False,
        "contracts_modified": False,
        "decisions_modified": False,
        "m3_artifacts_modified": False,
        "m4_3_plus_started": False,
        "absolute_coordinate_leakage": False,
        "hidden_wavelength_default": False,
        "runtime_wavelength_sorting": False,
        "padding_nonzero": False,
        "trained_checkpoint_created": False,
        "production_feature_artifact_created": False,
    }
    fixture_result = {
        "schema_id": "scene.m4.relative_fixture.v1",
        "stage_id": "M4.2",
        "wavelengths_m": list(generate_relative_wavelengths().tolist()),
        "expected_component_order": "[sin(px), cos(px), sin(py), cos(py)]",
        "relative_code_dim": 64,
        "relative_embedding_dim": 64,
    }
    manifest = {
        "schema_id": "scene.m4.stage_manifest.v1",
        "milestone": "M4",
        "stage_id": "M4.2",
        "stage_name": "Relative Position Encoder",
        "stage_status": "PASS" if valid else "FAIL",
        "run_id": run_id,
        "started_at_kst": started_at,
        "config_path": str(config_path),
        "config_hash": _sha256_json(config),
        "m4_1_evidence_dir": str(m4_1_dir),
        "approved_decisions": list(M4_APPROVED_DECISIONS),
        "architecture": architecture,
        "created_files": [
            "m4_2_stage_manifest.json",
            "m4_2_acceptance_result.json",
            "m4_2_audit_result.json",
            "m4_2_wavelength_fixture.json",
            "m4_2_architecture_metadata.json",
            "m4_2_stage_report.md",
            "M4_2_PASS",
        ],
        "forbidden_outputs": [
            "seoul_full_object_e_rel",
            "canonical_feature_parquet",
            "production_feature_cache",
            "training_checkpoint",
            "trained_model",
            "m4_3_geometry_output",
            "m5_plus_artifact",
        ],
        "next_stage": "M4.3" if valid else None,
        "auto_continue": False,
    }
    report = "\n".join(
        [
            "# M4.2 Relative Position Encoder Stage Report",
            "",
            "## Scope",
            "",
            "M4.2 implemented and validated the D-001 relative-position encoder "
            "only. M4.3 geometry Fourier and later stages were not started.",
            "",
            "## Final Status",
            "",
            "```text",
            "M4.2",
            "PASS" if valid else "FAIL",
            "",
            "M4.3",
            "READY" if valid else "BLOCKED",
            "",
            "AUTO_CONTINUE",
            "false",
            "```",
            "",
        ]
    )

    _write_json(stage_dir / "m4_2_acceptance_result.json", acceptance_result)
    _write_json(stage_dir / "m4_2_audit_result.json", audit_result)
    _write_json(stage_dir / "m4_2_wavelength_fixture.json", fixture_result)
    _write_json(stage_dir / "m4_2_architecture_metadata.json", architecture)
    _write_json(stage_dir / "m4_2_stage_manifest.json", manifest)
    _write_text(stage_dir / "m4_2_stage_report.md", report)
    if valid:
        _write_text(stage_dir / "M4_2_PASS", "PASS\n")

    return {
        "status": "PASS" if valid else "FAIL",
        "stage_id": "M4.2",
        "run_id": run_id,
        "stage_dir": str(stage_dir),
        "manifest": str(stage_dir / "m4_2_stage_manifest.json"),
        "acceptance_result": str(stage_dir / "m4_2_acceptance_result.json"),
        "audit_result": str(stage_dir / "m4_2_audit_result.json"),
        "pass_marker": str(stage_dir / "M4_2_PASS") if valid else None,
        "auto_continue": False,
    }
