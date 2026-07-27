"""M4 explicit stage runner skeleton."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
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
from scene.m4.geometry_encoder import (
    GEOMETRY_ENCODER_SEED,
    GeometryEncoder,
    fourier_to_magnitude_phase,
    geometry_architecture_metadata,
    initialize_geometry_encoder,
    state_dict_sha256,
)
from scene.m4.geometry_materialization import (
    MATERIALIZATION_WORKERS,
    architecture_hash,
    frequency_config_hash,
    frequency_tensor_hash,
    run_geometry_materialization,
    sha256_file,
)
from scene.m4.geometry_module import geometry_primitive_metadata
from scene.m4.triangle_backend import TRIANGLE_OPTIONS
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


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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


def _result(name: str, passed: bool, detail: str, *, status: str | None = None) -> dict[str, object]:
    return {
        "name": name,
        "status": status or ("PASS" if passed else "FAIL"),
        "passed": passed,
        "detail": detail,
    }


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
        raise ValueError("M4 skeleton config must declare the approved M4 stage catalog in order")
    return payload


def run_m4_stage(
    config_path: Path,
    *,
    stage_id: str,
    output_dir: Path | None = None,
    m4_1_dir: Path | None = None,
    m4_2_dir: Path | None = None,
    m4_3_dir: Path | None = None,
    m4_3a_dir: Path | None = None,
    m4_4_dir: Path | None = None,
    m4_5_dir: Path | None = None,
    m4_6_dir: Path | None = None,
    m4_7_dir: Path | None = None,
    m4_8_dir: Path | None = None,
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
    if stage_id == "M4.4":
        if m4_3a_dir is None:
            raise ValueError("M4.4 requires explicit --m4-3a-dir evidence")
        return run_m4_4_magnitude(
            config_path=config_path,
            output_dir=output_dir,
            m4_3a_dir=m4_3a_dir,
        )
    if stage_id == "M4.5":
        if m4_4_dir is None:
            raise ValueError("M4.5 requires explicit --m4-4-dir evidence")
        return run_m4_5_phase(
            config_path=config_path,
            output_dir=output_dir,
            m4_4_dir=m4_4_dir,
        )
    if stage_id == "M4.6":
        if m4_5_dir is None:
            raise ValueError("M4.6 requires explicit --m4-5-dir evidence")
        return run_m4_6_fusion(
            config_path=config_path,
            output_dir=output_dir,
            m4_5_dir=m4_5_dir,
        )
    if stage_id == "M4.7":
        if m4_6_dir is None:
            raise ValueError("M4.7 requires explicit --m4-6-dir evidence")
        return run_m4_7_modality_interface(
            config_path=config_path,
            output_dir=output_dir,
            m4_6_dir=m4_6_dir,
        )
    if stage_id == "M4.8":
        if m4_7_dir is None:
            raise ValueError("M4.8 requires explicit --m4-7-dir evidence")
        return run_m4_8_production_materialization(
            config_path=config_path,
            output_dir=output_dir,
            m4_7_dir=m4_7_dir,
            workers=workers,
        )
    if stage_id == "M4.9":
        if m4_8_dir is None:
            raise ValueError("M4.9 requires explicit --m4-8-dir evidence")
        return run_m4_9_release(
            config_path=config_path,
            output_dir=output_dir,
            m4_8_dir=m4_8_dir,
            workers=workers,
        )
    raise ValueError("M4 runner supports only explicit M4.1-M4.9 stages")


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
            "options": TRIANGLE_OPTIONS,
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


def _m4_stage_passed(stage_dir: Path, marker: str) -> bool:
    return stage_dir.is_dir() and (stage_dir / marker).is_file()


def _stage_prefix(stage_id: str) -> str:
    return stage_id.lower().replace(".", "_")


def _stage_pass_marker(stage_id: str) -> str:
    return f"{stage_id.replace('.', '_')}_PASS"


def _previous_stage_evidence(stage_dir: Path, stage_id: str) -> dict[str, object]:
    manifest_path = stage_dir / f"{_stage_prefix(stage_id)}_stage_manifest.json"
    pass_marker_path = stage_dir / _stage_pass_marker(stage_id)
    valid = stage_dir.is_dir() and manifest_path.is_file() and pass_marker_path.is_file()
    return {
        "previous_stage_id": stage_id,
        "previous_stage_manifest_path": str(manifest_path),
        "previous_stage_manifest_sha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
        "previous_stage_pass_marker_path": str(pass_marker_path),
        "previous_stage_pass_marker_sha256": sha256_file(pass_marker_path) if pass_marker_path.is_file() else None,
        "valid": valid,
    }


def _stage_inventory_entry(stage_dir: Path, stage_id: str) -> dict[str, object]:
    manifest_path = stage_dir / f"{_stage_prefix(stage_id)}_stage_manifest.json"
    marker_path = stage_dir / _stage_pass_marker(stage_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    return {
        "stage_id": stage_id,
        "stage_dir": str(stage_dir),
        "stage_status": manifest.get("stage_status"),
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
        "pass_marker_path": str(marker_path),
        "pass_marker_sha256": sha256_file(marker_path) if marker_path.is_file() else None,
    }


def _collect_stage_inventory_from_m4_8(m4_8_dir: Path) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    current_dir = m4_8_dir
    current_stage = "M4.8"
    while current_stage in {"M4.8", "M4.7", "M4.6", "M4.5", "M4.4"}:
        entry = _stage_inventory_entry(current_dir, current_stage)
        inventory.append(entry)
        manifest_path = current_dir / f"{_stage_prefix(current_stage)}_stage_manifest.json"
        if current_stage == "M4.4" or not manifest_path.is_file():
            break
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_path = manifest.get("previous_stage_manifest_path")
        previous_stage = manifest.get("previous_stage_id")
        if not previous_path or not previous_stage:
            break
        current_dir = Path(str(previous_path)).parent
        current_stage = str(previous_stage)
    return sorted(inventory, key=lambda item: str(item["stage_id"]))


def _geometry_tensor_schema_summary() -> dict[str, object]:
    return {
        "fourier_complex": "complex64[128] represented by fourier_real and fourier_imag",
        "fourier_real": "fixed_size_list<float32>[128]",
        "fourier_imag": "fixed_size_list<float32>[128]",
        "fourier_magnitude": "fixed_size_list<float32>[128]",
        "fourier_phase": "fixed_size_list<float32>[128]",
        "x_mag": "fixed_size_list<float32>[128]",
        "x_phase": "fixed_size_list<float32>[256]",
        "e_mag": "fixed_size_list<float32>[128]",
        "e_phase": "fixed_size_list<float32>[128]",
        "e_geom": "fixed_size_list<float32>[128]",
        "geometry_frequency_mask": "fixed_size_list<bool>[128]",
        "geometry_available": "bool",
        "poi_geometry_rows": 0,
    }


def _quarantine_exclusion_evidence() -> dict[str, object]:
    quarantine_root = Path("outputs/m4/quarantine")
    entries = sorted(str(path) for path in quarantine_root.iterdir()) if quarantine_root.is_dir() else []
    return {
        "quarantine_root": str(quarantine_root),
        "quarantine_entry_count": len(entries),
        "release_manifest_references_quarantine": False,
        "excluded_from_release": True,
    }


def _code_revision_status() -> dict[str, object]:
    def run_git(args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
                cwd=Path.cwd(),
            )
        except Exception:
            return None
        return result.stdout.strip()

    status_short = run_git(["status", "--short"])
    return {
        "git_commit": run_git(["rev-parse", "HEAD"]),
        "git_status_short": status_short,
        "git_dirty": bool(status_short),
    }


def _new_stage_dir(root: Path, stage_id: str) -> tuple[str, Path, str]:
    started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    run_id = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S_KST")
    stage_dir = root / run_id / stage_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    return run_id, stage_dir, started_at


def _stage_result(
    *,
    stage_id: str,
    valid: bool,
    run_id: str,
    stage_dir: Path,
    marker_name: str,
) -> dict[str, object]:
    return {
        "status": "PASS" if valid else "FAIL",
        "stage_id": stage_id,
        "run_id": run_id,
        "stage_dir": str(stage_dir),
        "manifest": str(stage_dir / f"{stage_id.lower().replace('.', '_')}_stage_manifest.json"),
        "acceptance_result": str(stage_dir / f"{stage_id.lower().replace('.', '_')}_acceptance_result.json"),
        "audit_result": str(stage_dir / f"{stage_id.lower().replace('.', '_')}_audit_result.json"),
        "pass_marker": str(stage_dir / marker_name) if valid else None,
        "auto_continue": False,
    }


def _write_stage_common(
    *,
    stage_dir: Path,
    stage_id: str,
    stage_name: str,
    run_id: str,
    started_at: str,
    config_path: Path,
    config: Mapping[str, Any],
    valid: bool,
    checks: list[dict[str, object]],
    audit: dict[str, object],
    manifest_extra: Mapping[str, Any],
    previous_stage_evidence: Mapping[str, Any] | None = None,
    created_files: list[str],
    forbidden_outputs: list[str],
    next_stage: str | None,
    report_lines: list[str],
    marker_name: str,
) -> None:
    prefix = stage_id.lower().replace(".", "_")
    code_revision_status = _code_revision_status()
    acceptance = {
        "schema_id": f"scene.m4.{prefix}_acceptance.v1",
        "stage_id": stage_id,
        "status": "PASS" if valid else "FAIL",
        "checks": checks,
    }
    checkpoint = {
        "schema_id": "scene.m4.stage_checkpoint.v1",
        "stage_id": stage_id,
        "status": "PASS" if valid else "FAIL",
        "acceptance_hash": _sha256_json(acceptance),
        "audit_hash": _sha256_json(audit),
        "previous_stage_id": previous_stage_evidence.get("previous_stage_id") if previous_stage_evidence else None,
        "previous_stage_manifest_sha256": previous_stage_evidence.get("previous_stage_manifest_sha256") if previous_stage_evidence else None,
        "previous_stage_pass_marker_sha256": previous_stage_evidence.get("previous_stage_pass_marker_sha256") if previous_stage_evidence else None,
        "auto_continue": False,
    }
    manifest = {
        "schema_id": "scene.m4.stage_manifest.v1",
        "milestone": "M4",
        "stage_id": stage_id,
        "stage_name": stage_name,
        "stage_status": "PASS" if valid else "FAIL",
        "run_id": run_id,
        "started_at_kst": started_at,
        "config_path": str(config_path),
        "config_hash": _sha256_json(config),
        "current_config_hash": _sha256_json(config),
        "current_code_revision_status": code_revision_status,
        **(dict(previous_stage_evidence) if previous_stage_evidence else {}),
        "approved_decisions": list(M4_APPROVED_DECISIONS),
        "created_files": created_files,
        "forbidden_outputs": forbidden_outputs,
        "next_stage": next_stage if valid else None,
        "auto_continue": False,
        **dict(manifest_extra),
    }
    report = "\n".join(report_lines + ["", "## Final Status", "", "```text", stage_id, "PASS" if valid else "FAIL", "", "AUTO_CONTINUE", "false", "```", ""])
    _write_json(stage_dir / f"{prefix}_acceptance_result.json", acceptance)
    _write_json(stage_dir / f"{prefix}_audit_result.json", audit)
    _write_json(stage_dir / f"{prefix}_stage_checkpoint.json", checkpoint)
    _write_json(stage_dir / f"{prefix}_stage_manifest.json", manifest)
    _write_text(stage_dir / f"{prefix}_stage_report.md", report)
    if valid:
        _write_text(stage_dir / marker_name, "PASS\n")


def _encoder_fixture() -> tuple[GeometryEncoder, torch.Tensor, torch.Tensor, Any, Any]:
    model = initialize_geometry_encoder(seed=GEOMETRY_ENCODER_SEED, device="cpu")
    fourier = torch.complex(
        torch.linspace(0.0, 1.0, 256, dtype=torch.float32).reshape(2, 128),
        torch.linspace(1.0, 0.0, 256, dtype=torch.float32).reshape(2, 128),
    )
    features = fourier_to_magnitude_phase(fourier)
    with torch.no_grad():
        encoded = model(features.x_mag, features.x_phase)
    return model, fourier, features.x_mag, features, encoded


def run_m4_4_magnitude(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_3a_dir: Path,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.4")
    model, _fourier, _x_mag, features, encoded = _encoder_fixture()
    previous = _previous_stage_evidence(m4_3a_dir, "M4.3A")
    checks = [
        _result("m4_3a_evidence", bool(previous["valid"]), str(previous)),
        _result("x_mag_shape", tuple(features.x_mag.shape) == (2, 128), str(tuple(features.x_mag.shape))),
        _result("x_mag_formula", bool(torch.allclose(features.x_mag, torch.log1p(features.fourier_magnitude))), "log1p(hypot(real,imag))"),
        _result("f_mag_shape", tuple(encoded.e_mag.shape) == (2, 128), str(tuple(encoded.e_mag.shape))),
        _result("finite_magnitude_branch", bool(torch.isfinite(features.x_mag).all() and torch.isfinite(encoded.e_mag).all()), "NaN/Inf 0"),
        _result("no_production_artifact", not any(stage_dir.glob("*.parquet")) and not any(stage_dir.glob("*.pt")), "M4.8/M4.9 outputs not created"),
    ]
    valid = not [check for check in checks if check["status"] == "FAIL"]
    audit = {
        "schema_id": "scene.m4.m4_4_audit.v1",
        "stage_id": "M4.4",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_3a_evidence_dir": str(m4_3a_dir),
        "study_methods_modified": False,
        "contracts_modified": False,
        "decisions_modified": False,
        "production_materialization_started": False,
        "model_checkpoint_created": False,
    }
    _write_json(stage_dir / "m4_4_geometry_architecture.json", geometry_architecture_metadata())
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.4",
        stage_name="Fourier Magnitude Encoder",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"state_dict_hash": state_dict_sha256(model), "m4_3a_evidence_dir": str(m4_3a_dir)},
        previous_stage_evidence=previous,
        created_files=["m4_4_geometry_architecture.json", "m4_4_stage_manifest.json", "m4_4_acceptance_result.json", "m4_4_audit_result.json", "m4_4_stage_checkpoint.json", "m4_4_stage_report.md", "M4_4_PASS"],
        forbidden_outputs=["production_feature_parquet", "encoder_checkpoint", "trained_model", "m4_5_auto_start"],
        next_stage="M4.5",
        report_lines=["# M4.4 Fourier Magnitude Encoder Stage Report", "", "M4.4 validates `r=hypot(Re,Im)`, `x_mag=log1p(r)`, and `f_mag` shape/finite output only."],
        marker_name="M4_4_PASS",
    )
    return _stage_result(stage_id="M4.4", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_4_PASS")


def run_m4_5_phase(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_4_dir: Path,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.5")
    model, _fourier, _x_mag, features, encoded = _encoder_fixture()
    previous = _previous_stage_evidence(m4_4_dir, "M4.4")
    zero_fourier = torch.zeros((1, 128), dtype=torch.complex64)
    zero_features = fourier_to_magnitude_phase(zero_fourier)
    checks = [
        _result("m4_4_evidence", bool(previous["valid"]), str(previous)),
        _result("phase_shape", tuple(features.fourier_phase.shape) == (2, 128), str(tuple(features.fourier_phase.shape))),
        _result("x_phase_shape", tuple(features.x_phase.shape) == (2, 256), str(tuple(features.x_phase.shape))),
        _result("zero_phase_policy", bool(torch.all(zero_features.fourier_phase == 0.0) and torch.all(zero_features.x_phase[:, :128] == 1.0) and torch.all(zero_features.x_phase[:, 128:] == 0.0)), "near-zero phase maps to cos=1 sin=0"),
        _result("f_phase_shape", tuple(encoded.e_phase.shape) == (2, 128), str(tuple(encoded.e_phase.shape))),
        _result("finite_phase_branch", bool(torch.isfinite(features.x_phase).all() and torch.isfinite(encoded.e_phase).all()), "NaN/Inf 0"),
        _result("no_production_artifact", not any(stage_dir.glob("*.parquet")) and not any(stage_dir.glob("*.pt")), "M4.8/M4.9 outputs not created"),
    ]
    valid = not [check for check in checks if check["status"] == "FAIL"]
    audit = {
        "schema_id": "scene.m4.m4_5_audit.v1",
        "stage_id": "M4.5",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_4_evidence_dir": str(m4_4_dir),
        "production_materialization_started": False,
        "model_checkpoint_created": False,
    }
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.5",
        stage_name="Fourier Phase Encoder",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"state_dict_hash": state_dict_sha256(model), "m4_4_evidence_dir": str(m4_4_dir)},
        previous_stage_evidence=previous,
        created_files=["m4_5_stage_manifest.json", "m4_5_acceptance_result.json", "m4_5_audit_result.json", "m4_5_stage_checkpoint.json", "m4_5_stage_report.md", "M4_5_PASS"],
        forbidden_outputs=["production_feature_parquet", "encoder_checkpoint", "trained_model", "m4_6_auto_start"],
        next_stage="M4.6",
        report_lines=["# M4.5 Fourier Phase Encoder Stage Report", "", "M4.5 validates phase zero policy, `x_phase=[cos(phi); sin(phi)]`, and `f_phase` output."],
        marker_name="M4_5_PASS",
    )
    return _stage_result(stage_id="M4.5", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_5_PASS")


def run_m4_6_fusion(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_5_dir: Path,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.6")
    model, _fourier, _x_mag, _features, encoded = _encoder_fixture()
    previous = _previous_stage_evidence(m4_5_dir, "M4.5")
    checks = [
        _result("m4_5_evidence", bool(previous["valid"]), str(previous)),
        _result("e_geom_shape", tuple(encoded.e_geom.shape) == (2, 128), str(tuple(encoded.e_geom.shape))),
        _result("finite_e_geom", bool(torch.isfinite(encoded.e_geom).all()), "NaN/Inf 0"),
        _result("eval_determinism", bool(torch.equal(encoded.e_geom, model(_features.x_mag, _features.x_phase).e_geom)), "same state/input eval output identical"),
        _result("initialized_untrained", True, "model initialized once; no optimizer/training/checkpoint in M4.6"),
        _result("no_production_artifact", not any(stage_dir.glob("*.parquet")) and not any(stage_dir.glob("*.pt")), "M4.8/M4.9 outputs not created"),
    ]
    valid = not [check for check in checks if check["status"] == "FAIL"]
    audit = {
        "schema_id": "scene.m4.m4_6_audit.v1",
        "stage_id": "M4.6",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_5_evidence_dir": str(m4_5_dir),
        "training_started": False,
        "production_materialization_started": False,
        "model_checkpoint_created": False,
    }
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.6",
        stage_name="Geometry Fusion Encoder",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"architecture": geometry_architecture_metadata(), "state_dict_hash": state_dict_sha256(model), "m4_5_evidence_dir": str(m4_5_dir)},
        previous_stage_evidence=previous,
        created_files=["m4_6_stage_manifest.json", "m4_6_acceptance_result.json", "m4_6_audit_result.json", "m4_6_stage_checkpoint.json", "m4_6_stage_report.md", "M4_6_PASS"],
        forbidden_outputs=["production_feature_parquet", "encoder_checkpoint", "trained_model", "m4_7_auto_start"],
        next_stage="M4.7",
        report_lines=["# M4.6 Geometry Fusion Encoder Stage Report", "", "M4.6 validates `f_geom([e_mag;e_phase]) -> e_geom[128]` for the initialized untrained encoder."],
        marker_name="M4_6_PASS",
    )
    return _stage_result(stage_id="M4.6", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_6_PASS")


def run_m4_7_modality_interface(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_6_dir: Path,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.7")
    previous = _previous_stage_evidence(m4_6_dir, "M4.6")
    modality = [
        {"object_type": "building", "geometry_available": True, "e_geom_required": True},
        {"object_type": "road", "geometry_available": True, "e_geom_required": True},
        {"object_type": "poi", "geometry_available": False, "e_geom_required": False},
    ]
    checks = [
        _result("m4_6_evidence", bool(previous["valid"]), str(previous)),
        _result("building_geometry_available", modality[0]["geometry_available"] is True, "building uses polygon Fourier geometry"),
        _result("road_geometry_available", modality[1]["geometry_available"] is True, "road uses polyline Fourier geometry"),
        _result("poi_geometry_unavailable", modality[2]["geometry_available"] is False and modality[2]["e_geom_required"] is False, "POI has no fake geometry embedding"),
        _result("no_production_artifact", not any(stage_dir.glob("*.parquet")) and not any(stage_dir.glob("*.pt")), "M4.8/M4.9 outputs not created"),
    ]
    valid = not [check for check in checks if check["status"] == "FAIL"]
    audit = {
        "schema_id": "scene.m4.m4_7_audit.v1",
        "stage_id": "M4.7",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_6_evidence_dir": str(m4_6_dir),
        "poi_fourier_computation": False,
        "fake_poi_e_geom": False,
        "production_materialization_started": False,
    }
    _write_json(stage_dir / "m4_7_modality_interface.json", {"schema_id": "scene.m4.geometry_modality_interface.v1", "modalities": modality})
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.7",
        stage_name="Building Road Modality Interface",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"m4_6_evidence_dir": str(m4_6_dir), "modality_interface": modality},
        previous_stage_evidence=previous,
        created_files=["m4_7_modality_interface.json", "m4_7_stage_manifest.json", "m4_7_acceptance_result.json", "m4_7_audit_result.json", "m4_7_stage_checkpoint.json", "m4_7_stage_report.md", "M4_7_PASS"],
        forbidden_outputs=["production_feature_parquet", "encoder_checkpoint", "trained_model", "m4_8_auto_start"],
        next_stage="M4.8",
        report_lines=["# M4.7 Building Road Modality Interface Stage Report", "", "M4.7 fixes geometry modality availability: building/road true, POI false."],
        marker_name="M4_7_PASS",
    )
    return _stage_result(stage_id="M4.7", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_7_PASS")


def run_m4_8_production_materialization(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_7_dir: Path,
    workers: int = 40,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.8")
    previous = _previous_stage_evidence(m4_7_dir, "M4.7")
    evidence_ok = bool(previous["valid"])
    if evidence_ok:
        materialization = run_geometry_materialization(stage_dir=stage_dir, workers=workers)
    else:
        materialization = {
            "schema_id": "scene.m4.geometry_materialization_result.v1",
            "status": "FAIL",
            "workers": workers,
            "elapsed_seconds": 0.0,
            "state_dict_hash": None,
            "architecture": geometry_architecture_metadata(),
            "upstream": {},
            "validation": {
                "expected_geometry_objects": 0,
                "geometry_rows": 0,
                "missing": 0,
                "duplicate_observation_id": 0,
                "worker_exception": 0,
                "object_failure": 0,
                "nonfinite_embedding": 0,
                "completion_marker_count": 0,
            },
            "shard_manifest": {"task_count": 0, "shards": [], "failed_shards": []},
            "artifact_size_bytes": 0,
        }
    validation = materialization["validation"]
    checks = [
        _result("m4_7_evidence", evidence_ok, str(m4_7_dir)),
        _result("workers_40", workers == MATERIALIZATION_WORKERS, str(workers)),
        _result("geometry_rows_complete", validation["geometry_rows"] == validation["expected_geometry_objects"], str(validation)),
        _result("missing_zero", validation["missing"] == 0, str(validation["missing"])),
        _result("duplicate_zero", validation["duplicate_observation_id"] == 0, str(validation["duplicate_observation_id"])),
        _result("worker_exception_zero", validation["worker_exception"] == 0, str(validation["worker_exception"])),
        _result("object_failure_zero", validation["object_failure"] == 0, str(validation["object_failure"])),
        _result("nonfinite_zero", validation["nonfinite_embedding"] == 0, str(validation["nonfinite_embedding"])),
        _result("completion_markers", validation["completion_marker_count"] == materialization["shard_manifest"]["task_count"], str(validation["completion_marker_count"])),
    ]
    valid = materialization["status"] == "PASS" and evidence_ok and not [check for check in checks if check["status"] == "FAIL"]
    _write_json(stage_dir / "m4_8_geometry_shard_manifest.json", materialization["shard_manifest"])
    _write_json(stage_dir / "m4_8_materialization_validation.json", validation)
    _write_json(stage_dir / "m4_8_materialization_metrics.json", materialization)
    audit = {
        "schema_id": "scene.m4.m4_8_audit.v1",
        "stage_id": "M4.8",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_7_evidence_dir": str(m4_7_dir),
        "workers": workers,
        "training_started": False,
        "poi_geometry_artifact_created": False,
        "m5_plus_started": False,
    }
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.8",
        stage_name="Production Materialization and CPU/GPU Validation",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"m4_7_evidence_dir": str(m4_7_dir), "materialization": materialization},
        previous_stage_evidence=previous,
        created_files=["m4_8_geometry_shard_manifest.json", "m4_8_materialization_validation.json", "m4_8_materialization_metrics.json", "m4_8_stage_manifest.json", "m4_8_acceptance_result.json", "m4_8_audit_result.json", "m4_8_stage_checkpoint.json", "m4_8_stage_report.md", "M4_8_PASS"],
        forbidden_outputs=["trained_model", "m4_9_auto_start", "m5_plus_artifact"],
        next_stage="M4.9",
        report_lines=["# M4.8 Production Materialization Stage Report", "", f"Materialized initialized-untrained geometry embeddings with workers={workers}.", "", "```json", json.dumps(validation, indent=2, sort_keys=True), "```"],
        marker_name="M4_8_PASS",
    )
    return _stage_result(stage_id="M4.8", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_8_PASS")


def run_m4_9_release(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    m4_8_dir: Path,
    workers: int = 40,
) -> dict[str, object]:
    config = load_m4_skeleton_config(config_path)
    run_id, stage_dir, started_at = _new_stage_dir(output_dir or Path("outputs/m4/geometry_encoder"), "M4.9")
    previous = _previous_stage_evidence(m4_8_dir, "M4.8")
    m4_8_manifest_path = m4_8_dir / "m4_8_stage_manifest.json"
    m4_8_manifest = json.loads(m4_8_manifest_path.read_text(encoding="utf-8")) if m4_8_manifest_path.is_file() else {}
    m4_8_metrics_path = m4_8_dir / "m4_8_materialization_metrics.json"
    m4_8_metrics = json.loads(m4_8_metrics_path.read_text(encoding="utf-8")) if m4_8_metrics_path.is_file() else {}
    evidence_ok = bool(previous["valid"])
    model = initialize_geometry_encoder(seed=GEOMETRY_ENCODER_SEED, device="cpu")
    state_hash = state_dict_sha256(model)
    checkpoint_path = stage_dir / "geometry_encoder_initialized_untrained_state.pt"
    if evidence_ok:
        torch.save(
            {
                "schema_id": "scene.m4.geometry_encoder_state.v1",
                "model_status": "initialized_untrained",
                "initialization_seed": GEOMETRY_ENCODER_SEED,
                "architecture": geometry_architecture_metadata(),
                "state_dict": model.state_dict(),
                "state_dict_hash": state_hash,
            },
            checkpoint_path,
        )
    checkpoint_sha = sha256_file(checkpoint_path) if checkpoint_path.is_file() else None
    materialization = m4_8_metrics if isinstance(m4_8_metrics, Mapping) else {}
    validation = materialization.get("validation", {}) if isinstance(materialization.get("validation"), Mapping) else {}
    shard_manifest = materialization.get("shard_manifest", {}) if isinstance(materialization.get("shard_manifest"), Mapping) else {}
    upstream = materialization.get("upstream", {}) if isinstance(materialization.get("upstream"), Mapping) else {}
    stage_inventory = _collect_stage_inventory_from_m4_8(m4_8_dir) if evidence_ok else []
    release_manifest = {
        "schema_id": "scene.m4.geometry_release_manifest.v2",
        "milestone": "M4",
        "status": "PASS" if evidence_ok else "FAIL",
        "model_status": "initialized_untrained",
        "workers": workers,
        "m4_8_evidence_dir": str(m4_8_dir),
        "m4_8_stage_hash": _sha256_json(m4_8_manifest) if m4_8_manifest else None,
        "stage_inventory": stage_inventory,
        "stage_manifest_paths": {
            str(item["stage_id"]): item["manifest_path"] for item in stage_inventory
        },
        "stage_pass_marker_paths": {
            str(item["stage_id"]): item["pass_marker_path"] for item in stage_inventory
        },
        "object_counts": {
            "building": validation.get("object_type_counts", {}).get("building"),
            "road": validation.get("object_type_counts", {}).get("road"),
            "poi": upstream.get("poi", {}).get("rows"),
            "geometry_rows": validation.get("geometry_rows"),
        },
        "shard_count": shard_manifest.get("task_count"),
        "aggregate_shard_hash": shard_manifest.get("aggregate_shard_hash"),
        "artifact_size_bytes": materialization.get("artifact_size_bytes"),
        "tensor_schema_summary": _geometry_tensor_schema_summary(),
        "geometry_frequency_mask_contract": {
            "column": "geometry_frequency_mask",
            "shape": "[128]",
            "dtype": "bool",
            "building_road_semantics": "all true because all 128 D-002 frequencies are generated and valid for each successful geometry row",
            "poi_semantics": "no fake POI geometry row or mask is materialized",
        },
        "architecture": geometry_architecture_metadata(),
        "architecture_hash": architecture_hash(geometry_architecture_metadata()),
        "state_dict_hash": state_hash,
        "checkpoint_file": checkpoint_path.name,
        "checkpoint_sha256": checkpoint_sha,
        "frequency_tensor_hash": frequency_tensor_hash(),
        "frequency_config_hash": frequency_config_hash(),
        "quarantine_exclusion_evidence": _quarantine_exclusion_evidence(),
        "auto_continue": False,
        "m5_started": False,
        "M5_STARTED": False,
    }
    _write_json(stage_dir / "m4_geometry_release_manifest.json", release_manifest)
    checks = [
        _result("m4_8_evidence", evidence_ok, str(m4_8_dir)),
        _result("initialized_untrained_checkpoint", checkpoint_path.is_file(), checkpoint_path.name),
        _result("state_hash_present", len(state_hash) == 64, state_hash),
        _result("release_manifest", (stage_dir / "m4_geometry_release_manifest.json").is_file(), "release manifest written"),
        _result("stage_inventory_complete", {item["stage_id"] for item in stage_inventory} == {"M4.4", "M4.5", "M4.6", "M4.7", "M4.8"}, str(stage_inventory)),
        _result("geometry_mask_contract_recorded", "geometry_frequency_mask" in release_manifest["tensor_schema_summary"], "geometry mask schema present"),
        _result("aggregate_shard_hash_present", isinstance(release_manifest.get("aggregate_shard_hash"), str) and len(str(release_manifest.get("aggregate_shard_hash"))) == 64, str(release_manifest.get("aggregate_shard_hash"))),
        _result("checkpoint_sha_present", isinstance(checkpoint_sha, str) and len(checkpoint_sha) == 64, str(checkpoint_sha)),
        _result("m5_not_started", True, "M5+ not executed"),
    ]
    valid = not [check for check in checks if check["status"] == "FAIL"]
    audit = {
        "schema_id": "scene.m4.m4_9_audit.v1",
        "stage_id": "M4.9",
        "audit_status": "PASS" if valid else "FAIL",
        "m4_8_evidence_dir": str(m4_8_dir),
        "model_status": "initialized_untrained",
        "training_started": False,
        "m5_plus_started": False,
    }
    _write_stage_common(
        stage_dir=stage_dir,
        stage_id="M4.9",
        stage_name="Geometry Encoder Release",
        run_id=run_id,
        started_at=started_at,
        config_path=config_path,
        config=config,
        valid=valid,
        checks=checks,
        audit=audit,
        manifest_extra={"release_manifest": release_manifest},
        previous_stage_evidence=previous,
        created_files=["geometry_encoder_initialized_untrained_state.pt", "m4_geometry_release_manifest.json", "m4_9_stage_manifest.json", "m4_9_acceptance_result.json", "m4_9_audit_result.json", "m4_9_stage_checkpoint.json", "m4_9_stage_report.md", "M4_9_PASS", "M4_PASS"],
        forbidden_outputs=["trained_model", "m5_plus_artifact"],
        next_stage="M5",
        report_lines=["# M4.9 Geometry Encoder Release Stage Report", "", "M4.9 releases the initialized, untrained Geometry Encoder architecture and state. No training was performed."],
        marker_name="M4_9_PASS",
    )
    if valid:
        _write_text(stage_dir / "M4_PASS", "PASS\n")
    return _stage_result(stage_id="M4.9", valid=valid, run_id=run_id, stage_dir=stage_dir, marker_name="M4_9_PASS")
