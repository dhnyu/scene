"""M1.9 full replay, audit, and release decision workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Mapping

from scene.core.config import load_config
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import KST, collect_run_metadata
from scene.inventory.hashing import sha256_file
from scene.release_validation.artifacts import resolve_reference_artifacts
from scene.release_validation.audit import (
    geometry_audit,
    hash_comparison,
    id_audit,
    manifest_audit,
    open_decisions,
    provenance_audit,
    repository_audit,
    schema_audit,
    storage_audit,
)
from scene.release_validation.exceptions import ReleaseValidationError
from scene.release_validation.replay import replay_pipeline
from scene.release_validation.reporting import write_release_report
from scene.release_validation.serialization import write_release_artifacts


def _files(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            result.extend(item for item in path.rglob("*") if item.is_file())
        else:
            raise ReleaseValidationError(f"immutable input is missing: {path}")
    return tuple(dict.fromkeys(result))


def _snapshot(paths: tuple[Path, ...]) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for path in _files(paths):
        stat = path.stat()
        values[str(path)] = {
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
            "size": stat.st_size,
        }
    return values


def _directory_size(path: Path) -> int:
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file()
    )


def _run(
    config_path: str | Path,
    *,
    started_at: datetime,
    log_level: str,
    verification: Mapping[str, object] | None,
) -> dict[str, object]:
    config_file = Path(config_path).expanduser().resolve(strict=False)
    config = load_config(config_file)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    if (
        config.district_assignment is None
        or config.scene_generation is None
        or config.miniature_dataset is None
    ):
        raise ReleaseValidationError("M1 release configuration is incomplete")
    metadata = collect_run_metadata(config, started_at=started_at)
    output = (
        config.paths.output_root / "release_validation" / metadata.run_id
    )
    output.mkdir(parents=True, exist_ok=False)
    logger = configure_logging(
        config.paths.logs_dir / f"{metadata.run_id}_m1_9_release.jsonl",
        metadata.run_id,
        level=log_level,
    )
    reference = resolve_reference_artifacts(config)
    immutable_roots = (
        config.paths.project_root / "study_methods.md",
        config_file,
        config.paths.canonical_schema,
        *(source.path for source in config.sources),
        config.district_assignment.canonical_boundary_path,
        config.scene_generation.assignment_lock_path,
        *(
            Path(value)
            for value in reference.to_dict().values()
        ),
    )
    before = _snapshot(tuple(immutable_roots))
    logger.info("M1.9 full CLI replay started")
    replay_started = time.perf_counter()
    replay = replay_pipeline(config, config_file, output)
    replay_elapsed = time.perf_counter() - replay_started

    logger.info("M1.9 hash and geometry audits started")
    comparison = hash_comparison(reference, replay.artifacts)
    geometry = geometry_audit(reference, replay.artifacts)
    reference_ids = id_audit(reference)
    replay_ids = id_audit(replay.artifacts)
    ids = {
        "content_match": comparison["stages"]["ids"]["match"],
        "reference": reference_ids,
        "replay": replay_ids,
        "valid": (
            reference_ids["valid"]
            and replay_ids["valid"]
            and comparison["stages"]["ids"]["match"]
        ),
    }
    reference_manifest = manifest_audit(config, reference)
    replay_manifest = manifest_audit(config, replay.artifacts)
    schema = schema_audit(config, reference, replay.artifacts)
    manifest = {
        "reference": reference_manifest,
        "replay": replay_manifest,
        "schema_chain_match": schema["valid"],
        "valid": (
            reference_manifest["valid"]
            and replay_manifest["valid"]
            and schema["valid"]
        ),
    }
    provenance = provenance_audit(config, reference)
    repository = repository_audit(config.paths.project_root)
    storage = storage_audit(
        reference,
        replay.artifacts,
        config.paths.output_root,
    )
    decisions = open_decisions(
        config.paths.project_root / "docs" / "decisions" / "decision_log.md"
    )
    after = _snapshot(tuple(immutable_roots))
    changed = {
        path: {"after": after[path], "before": before[path]}
        for path in before
        if before[path] != after[path]
    }
    read_only = {
        "changed_input_count": len(changed),
        "changed_inputs": changed,
        "unchanged": not changed,
    }
    pipeline_pass = (
        len(replay.stages) == 11
        and all(stage["status"] == "PASS" for stage in replay.stages)
    )
    determinism_pass = bool(
        comparison["all_match"] and geometry["all_content_match"]
    )
    verification_value = dict(verification or {})
    pytest_value = verification_value.get("pytest", {})
    test_pass = (
        isinstance(pytest_value, Mapping)
        and pytest_value.get("status") == "PASS"
        and int(pytest_value.get("failed", 1)) == 0
    )
    mechanical_pass = all(
        verification_value.get(key) == "PASS"
        for key in ("compileall", "yaml_parse")
    ) and (
        isinstance(verification_value.get("markdown_relative_links"), Mapping)
        and verification_value["markdown_relative_links"].get("status")
        == "PASS"
    )
    checks = {
        "cli": pipeline_pass,
        "determinism": determinism_pass,
        "documentation": (
            repository["broken_markdown_link_count"] == 0
        ),
        "geometry": geometry["all_valid"],
        "manifest": manifest["valid"],
        "provenance": provenance["valid"],
        "read_only": read_only["unchanged"],
        "repository": repository["valid"],
        "schema": schema["valid"],
        "stable_ids": ids["valid"],
        "storage": storage["valid"],
        "tests": test_pass and mechanical_pass,
    }
    release_categories = {
        "Infrastructure": "PASS"
        if checks["cli"] and checks["storage"]
        else "FAIL",
        "Canonical Data": "PASS"
        if checks["schema"] and checks["manifest"]
        else "FAIL",
        "Split": "PASS"
        if comparison["stages"]["split"]["match"]
        else "FAIL",
        "Scene": "PASS"
        if (
            comparison["stages"]["scene"]["match"]
            and geometry["layers"]["scene"]["content_match"]
        )
        else "FAIL",
        "Miniature": "PASS"
        if comparison["stages"]["miniature"]["match"]
        else "FAIL",
        "Determinism": "PASS" if determinism_pass else "FAIL",
        "Reproducibility": "PASS"
        if (
            determinism_pass
            and checks["provenance"]
            and checks["manifest"]
            and checks["schema"]
        )
        else "FAIL",
        "Repository": "PASS" if checks["repository"] else "FAIL",
        "Documentation": "PASS" if checks["documentation"] else "FAIL",
    }
    failures = [
        name for name, passed in checks.items() if not passed
    ]
    if decisions["m2_1_blocking_count"]:
        failures.append("m2_1_blocking_open_decisions")
    release_approved = not failures
    release_summary = {
        "acceptance_checks": checks,
        "failure_count": len(failures),
        "failures": failures,
        "m2_readiness": (
            "READY" if release_approved else "BLOCKED"
        ),
        "open_decisions": decisions,
        "release_candidate": (
            "A - M1 Release Candidate 승인; M2 시작 가능"
            if release_approved
            else "B - M1 Release Candidate 보류; 수정 필요한 항목 보고"
        ),
        "release_categories": release_categories,
        "schema_audit": schema,
        "storage_audit": storage,
    }
    performance = {
        "artifact_size_bytes": {
            key: _directory_size(Path(value))
            for key, value in replay.artifacts.to_dict().items()
            if Path(value).is_dir()
        },
        "pipeline_elapsed_seconds": replay_elapsed,
        "stages": [
            stage["performance"] for stage in replay.stages
        ],
    }
    pipeline = {
        **replay.to_dict(),
        "cli_count": len(replay.stages),
        "status": "PASS" if pipeline_pass else "FAIL",
    }
    payloads = {
        "pipeline_replay": pipeline,
        "hash_comparison": comparison,
        "geometry_audit": geometry,
        "id_audit": ids,
        "manifest_audit": manifest,
        "provenance_audit": provenance,
        "repository_audit": repository,
        "performance": performance,
        "release_summary": release_summary,
    }
    artifacts = write_release_artifacts(output, payloads)
    reports = write_release_report(
        config.paths.reports_dir,
        metadata,
        payloads=payloads,
        artifacts=artifacts,
        read_only=read_only,
        verification=verification_value,
    )
    logger.info(
        "M1.9 release validation completed",
        extra={
            "failure_count": len(failures),
            "release_candidate": release_summary["release_candidate"],
        },
    )
    return {
        "content_hash_replay": (
            "PASS" if comparison["all_match"] else "FAIL"
        ),
        "determinism": "PASS" if determinism_pass else "FAIL",
        "failure_count": len(failures),
        "geometry": "PASS" if checks["geometry"] else "FAIL",
        "json_report": str(reports.json),
        "manifest": "PASS" if checks["manifest"] else "FAIL",
        "markdown_report": str(reports.markdown),
        "m2_readiness": release_summary["m2_readiness"],
        "open_decision_count": decisions["m2_1_blocking_count"],
        "output_directory": str(output),
        "pipeline_replay": "PASS" if pipeline_pass else "FAIL",
        "provenance": "PASS" if checks["provenance"] else "FAIL",
        "release_candidate": release_summary["release_candidate"],
        "repository": "PASS" if checks["repository"] else "FAIL",
        "run_id": metadata.run_id,
        "stable_ids": "PASS" if checks["stable_ids"] else "FAIL",
        "status": "complete",
    }


def run_release_validation(
    config_path: str | Path,
    *,
    log_level: str = "INFO",
    started_at: datetime | None = None,
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run M1.9 and always emit a timestamp-matched failure report."""

    instant = started_at or datetime.now(tz=KST)
    try:
        return _run(
            config_path,
            started_at=instant,
            log_level=log_level,
            verification=verification,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        ReleaseValidationError,
    ) as exc:
        config = load_config(config_path)
        metadata = collect_run_metadata(config, started_at=instant)
        output = (
            config.paths.output_root
            / "release_validation"
            / metadata.run_id
        )
        generated = (
            [str(path) for path in output.rglob("*") if path.is_file()]
            if output.exists()
            else []
        )
        reports = write_reports(
            config.paths.reports_dir,
            f"{metadata.run_id}_m1_9_release_validation",
            title="M1.9 End-to-End Pipeline Validation",
            metadata=metadata,
            summary={
                "failure_count": 1,
                "failure_reason": str(exc),
                "generated_artifacts": generated,
                "m2_readiness": "BLOCKED",
                "release_candidate": (
                    "B - M1 Release Candidate 보류; 수정 필요한 항목 보고"
                ),
                "status": "failed",
            },
            sections=(
                ReportSection(
                    "Failure",
                    f"- Blocked stage: M1.9 execution\n"
                    f"- Actual observation: `{exc}`\n"
                    f"- Preserved artifacts: `{generated}`",
                ),
            ),
        )
        raise ReleaseValidationError(
            f"{exc}; failure report: {reports.markdown}"
        ) from exc
