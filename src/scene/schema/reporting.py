"""M1.3 canonical validation report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.schema.models import CanonicalRunResult
from scene.schema.serialization import CanonicalArtifactPaths


def _frame_table(result: CanonicalRunResult) -> str:
    rows = [
        "| Source | Frame | Kind | Rows | Columns | CRS | Geometry | Valid | Issues |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for frame in result.frames:
        issues = "; ".join(issue.message for issue in frame.issues) or "none"
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{frame.source_name}`",
                    f"`{frame.frame_name}`",
                    frame.source_kind,
                    str(frame.row_count),
                    str(frame.canonical_columns),
                    str(frame.crs),
                    str(frame.geometry_type),
                    str(frame.valid).lower(),
                    issues.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _mapping_rules() -> str:
    return "\n".join(
        [
            "- `A9 -> building_use`",
            "- `A11 -> building_structure`",
            "- `A16 -> building_height_m`",
            "- `A12 -> source_building_area_m2` is provenance only and is "
            "forbidden as model observed area.",
            "- Model observed building area is not created in M1.3; it is "
            "computed later from clipped observed geometry.",
        ]
    )


def write_canonical_report(
    result: CanonicalRunResult,
    artifacts: CanonicalArtifactPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    source_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write the required timestamped M1.3 Markdown and JSON reports."""

    return write_reports(
        report_dir,
        f"{result.run_id}_m1_3_canonical_schema",
        title="M1.3 Canonical Schema Validation & Mapping",
        metadata=metadata,
        summary={
            **result.to_dict(),
            "canonical_manifest_json": str(artifacts.manifest_json),
            "next_step": "M1.4 Data Adapters",
            "source_stat_changes": list(source_stat_changes),
            "status": (
                "complete"
                if result.schema_validation_passed and not source_stat_changes
                else "complete_with_validation_errors"
            ),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                f"Registered sources: `{result.source_count}`  \n"
                f"Mapped sources: `{result.mapped_source_count}`  \n"
                f"Validation failures: `{result.failure_count}`  \n"
                f"Schema validation: "
                f"`{'PASS' if result.schema_validation_passed else 'FAIL'}`",
            ),
            ReportSection("Source Mapping Results", _frame_table(result)),
            ReportSection("Building Mapping", _mapping_rules()),
            ReportSection(
                "Artifacts",
                f"- Canonical frame directory: `{artifacts.directory}`\n"
                f"- JSON manifest: `{artifacts.manifest_json}`",
            ),
            ReportSection(
                "Read-only Verification",
                "Source size or mtime_ns changes: "
                f"`{len(source_stat_changes)}`",
            ),
            ReportSection(
                "Scope",
                "The outputs are pre-ID Canonical DataFrames and their "
                "validation manifest. Raster pixels were not copied. No stable "
                "ID, district split, scene, footprint, clipping, relation, "
                "tensor, or model artifact was created.",
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
