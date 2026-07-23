"""M1.5 Stable IDs report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.id.provenance import ENTITY_SPECS, StableIdDataset
from scene.id.serialization import StableIdArtifacts
from scene.id.validator import StableIdValidation


def write_stable_id_report(
    dataset: StableIdDataset,
    validation: StableIdValidation,
    artifacts: StableIdArtifacts,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    input_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write timestamped Markdown and JSON reports."""

    status = (
        "complete"
        if validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    summary_lines = [
        f"- {spec.id_name}: `{validation.counts[spec.entity_type]}`"
        for spec in ENTITY_SPECS
    ]
    validation_lines = [
        f"- Duplicate IDs: `{validation.global_duplicate_id_count}`",
        f"- NULL IDs: `{validation.global_null_id_count}`",
        "- Deterministic regeneration: "
        f"`{validation.deterministic_regeneration}`",
        "- Source to canonical mapping: "
        f"`{validation.source_canonical_mapping_valid}`",
        f"- Provenance complete: `{validation.provenance_complete}`",
        f"- Provenance missing values: `{validation.provenance_missing_count}`",
        f"- Overall: `{'PASS' if validation.valid else 'FAIL'}`",
    ]
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_5_stable_ids",
        title="M1.5 Stable IDs",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "changed_files": [
                "README.md",
                "docs/contracts/acceptance_tests.md",
                "docs/contracts/id_and_provenance_contract.md",
                "docs/contracts/implementation_contract.md",
                "src/scene/cli.py",
                "src/scene/id/__init__.py",
                "src/scene/id/exceptions.py",
                "src/scene/id/generator.py",
                "src/scene/id/provenance.py",
                "src/scene/id/reader.py",
                "src/scene/id/reporting.py",
                "src/scene/id/serialization.py",
                "src/scene/id/validator.py",
                "src/scene/id/workflow.py",
                "tests/conftest.py",
                "tests/integration/test_stable_ids_actual.py",
                "tests/unit/test_stable_id_cli.py",
                "tests/unit/test_stable_id_generator.py",
                "tests/unit/test_stable_id_serialization.py",
                "tests/unit/test_stable_id_validation.py",
            ],
            "counts": validation.counts,
            "generation_digest": dataset.generation_digest,
            "input_stat_changes": list(input_stat_changes),
            "status": status,
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection("Summary", "\n".join(summary_lines)),
            ReportSection("Validation", "\n".join(validation_lines)),
            ReportSection(
                "Artifacts",
                "\n".join(
                    [
                        f"- IDs Parquet: `{artifacts.ids_parquet}`",
                        f"- Provenance Parquet: `{artifacts.provenance_parquet}`",
                        f"- JSON: `{artifacts.ids_json}`",
                    ]
                ),
            ),
            ReportSection(
                "Read-only Verification",
                "Source or canonical input size/mtime changes: "
                f"`{len(input_stat_changes)}`",
            ),
            ReportSection(
                "Scope",
                "Only stable building, road link, road node, POI and "
                "source-object IDs plus provenance were materialized. Scene, "
                "clip and relation factories remain pure functions. No "
                "district assignment, scene, clipping, observation geometry, "
                "relation graph, MAE, tensor, raster extraction, model input "
                "or training cache was created.",
            ),
            ReportSection(
                "Verification",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in (verification or {}).items()
                )
                or "Workflow validation only.",
            ),
        ),
    )
