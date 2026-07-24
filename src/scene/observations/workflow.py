"""M2.1 contract-only orchestration without project GIS access."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scene.core.config import load_config
from scene.core.logging import configure_logging
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import collect_run_metadata
from scene.observations.exceptions import ObservationContractError
from scene.observations.reference import validate_fixture
from scene.observations.schema import load_observation_schema


def _project_file(path: str | Path, project_root: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ObservationContractError(f"{label} is not a file: {resolved}")
    if not resolved.is_relative_to(project_root):
        raise ObservationContractError(
            f"{label} must be inside the project root: {resolved}"
        )
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_observation_contract(
    config_path: str | Path,
    *,
    schema_path: str | Path,
    fixture_path: str | Path,
    log_level: str = "INFO",
) -> dict[str, object]:
    """Validate the M2.1 contract and synthetic fixture only."""

    config = load_config(config_path)
    project_root = config.paths.project_root
    schema_file = _project_file(
        schema_path,
        project_root,
        "observation schema",
    )
    fixture_file = _project_file(
        fixture_path,
        project_root,
        "observation fixture",
    )
    contract_file = _project_file(
        project_root
        / "docs"
        / "contracts"
        / "scene_observation_contract.md",
        project_root,
        "observation contract",
    )

    metadata = collect_run_metadata(config)
    log_path = (
        config.paths.logs_dir
        / f"{metadata.run_id}_m2_1_scene_observation_contract.jsonl"
    )
    logger = configure_logging(log_path, metadata.run_id, level=log_level)
    logger.info("M2.1 observation contract validation started")

    schema = load_observation_schema(schema_file)
    validation = validate_fixture(schema, fixture_file)
    if not validation.valid:
        raise ObservationContractError(
            "M2.1 synthetic fixture validation failed"
        )

    summary = {
        "acceptance_criteria": "PASS",
        "blocked_next_decisions": ["D-004", "D-006"],
        "contract_path": str(contract_file),
        "contract_sha256": _sha256(contract_file),
        "fixture_content_hash": validation.content_hash,
        "fixture_observation_count": validation.observation_count,
        "fixture_path": str(fixture_file),
        "fixture_validation": "PASS",
        "forbidden_artifact_count": 0,
        "m2_2_road_materialization": "BLOCKED",
        "schema_path": str(schema.path),
        "schema_sha256": schema.sha256,
        "schema_validation": "PASS",
        "source_access": False,
        "status": "complete",
    }
    reports = write_reports(
        config.paths.reports_dir,
        f"{metadata.run_id}_m2_1_scene_observation_contract",
        title="M2.1 Scene Observation Contract",
        metadata=metadata,
        summary=summary,
        sections=(
            ReportSection(
                "Contract",
                "\n".join(
                    [
                        f"- Contract: `{contract_file}`",
                        f"- Contract SHA-256: `{summary['contract_sha256']}`",
                        f"- Schema: `{schema.path}`",
                        f"- Schema version: `{schema.schema_version}`",
                        f"- Schema SHA-256: `{schema.sha256}`",
                    ]
                ),
            ),
            ReportSection(
                "Fixture",
                "\n".join(
                    [
                        f"- Fixture: `{fixture_file}`",
                        f"- Vector observation rows: `{validation.observation_count}`",
                        f"- Content hash: `{validation.content_hash}`",
                        "- Source access: `False`",
                    ]
                ),
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    [
                        f"- Expected output: `{'PASS' if validation.expected_output_match else 'FAIL'}`",
                        f"- Deterministic regeneration: `{'PASS' if validation.deterministic_regeneration else 'FAIL'}`",
                        f"- Invalid geometry hard failure: `{validation.invalid_geometry_hard_failures}`",
                        f"- GeometryCollection hard failure: `{validation.geometry_collection_hard_failures}`",
                        f"- Overlapping-scene identity: `{'PASS' if validation.overlapping_object_distinct_observation_ids else 'FAIL'}`",
                        f"- Missing-state distinction: `{'PASS' if validation.raster_nodata_distinct else 'FAIL'}`",
                    ]
                ),
            ),
            ReportSection(
                "Acceptance Criteria",
                "M2.1 contract, schema, deterministic IDs, closed-set fixture, "
                "road part ordering, missing-state distinction and hard-failure "
                "geometry policy pass. No project GIS source, M1 artifact or "
                "raster pixel was read, and no observation dataset was "
                "materialized.",
            ),
            ReportSection(
                "Next Gate",
                "D-004 and D-006 remain Open. M2.1 contract validation is "
                "complete, but actual road observation materialization in "
                "M2.2 remains blocked. Raster extraction also remains subject "
                "to D-007 through D-009.",
            ),
        ),
    )
    logger.info("M2.1 observation contract validation completed")
    return {
        **summary,
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "run_id": metadata.run_id,
    }
