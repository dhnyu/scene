"""Human- and machine-readable M1.6 assignment report."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.split.provenance import (
    AssignmentValidation,
    BalancingStatistics,
    DistrictAssignment,
)
from scene.split.serialization import (
    AssignmentArtifacts,
    aggregate_split_statistics,
    artifact_hashes,
)
from scene.split.balancing import BalanceModel


def _district_list(assignment: DistrictAssignment, split: str) -> str:
    selected = assignment.frame.loc[
        assignment.frame["split"] == split,
        ["district_code", "district_name"],
    ]
    return ", ".join(
        f"{row.district_name} (`{row.district_code}`)"
        for row in selected.itertuples()
    )


def _statistics_table(
    aggregate: Mapping[str, Mapping[str, object]],
) -> str:
    rows = [
        "| Metric | Train | Validation | Test |",
        "| --- | ---: | ---: | ---: |",
    ]
    metrics = (
        "district_count",
        "area_km2",
        "eligible_scene_estimate",
        "building_count",
        "building_density_per_km2",
        "road_length_km",
        "road_density_km_per_km2",
        "poi_count",
        "poi_density_per_km2",
        "dem_mean_raw",
        "dem_std_raw",
        "context_cluster_count",
        "spatial_cluster_count",
        "radial_band_count",
        "connected_component_count",
    )
    for metric in metrics:
        rows.append(
            f"| `{metric}` | `{aggregate['train'][metric]}` | "
            f"`{aggregate['validation'][metric]}` | "
            f"`{aggregate['test'][metric]}` |"
        )
    return "\n".join(rows)


def write_assignment_report(
    assignment: DistrictAssignment,
    validation: AssignmentValidation,
    statistics: BalancingStatistics,
    model: BalanceModel,
    artifacts: AssignmentArtifacts,
    report_directory: Path,
    metadata: RunMetadata,
    *,
    input_changes: Mapping[str, object],
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    aggregate = aggregate_split_statistics(assignment, model, statistics)
    search = assignment.search
    return write_reports(
        report_directory,
        f"{metadata.run_id}_m1_6_district_assignment",
        title="M1.6 District Assignment",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "artifact_hashes": artifact_hashes(artifacts),
            "assignment_hash": assignment.assignment_hash,
            "assignment_config_hash": assignment.assignment_config_hash,
            "assignment_version": assignment.frame[
                "assignment_version"
            ].iloc[0],
            "balance_statistics_hash": assignment.balance_statistics_hash,
            "district_lists": {
                split: assignment.frame.loc[
                    assignment.frame["split"] == split,
                    "district_name",
                ].to_list()
                for split in ("train", "validation", "test")
            },
            "input_changes": dict(input_changes),
            "search": asdict(search),
            "split_statistics": aggregate,
            "status": "complete" if validation.valid else "failed",
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Assignment",
                f"### Train Districts\n\n{_district_list(assignment, 'train')}\n\n"
                "### Validation Districts\n\n"
                f"{_district_list(assignment, 'validation')}\n\n"
                "### Test Districts\n\n"
                f"{_district_list(assignment, 'test')}",
            ),
            ReportSection(
                "Balancing Statistics",
                _statistics_table(aggregate)
                + "\n\nLandcover values are raw code proportions without "
                "semantic labels. DEM values are raw with unresolved unit "
                "provenance. Full distributions are in "
                f"`{artifacts.balancing_statistics_json}`.",
            ),
            ReportSection(
                "Assignment Rationale",
                "- Candidate generation: deterministic seeded constrained "
                f"search, not direct random sampling (`{search.candidate_count}` "
                "evaluated candidates).\n"
                f"- Feasible candidates: `{search.feasible_candidate_count}`\n"
                f"- Selected weighted score: `{search.score}`\n"
                f"- Feasible median score: `{search.feasible_score_median}`\n"
                f"- Objective components: `{search.component_scores}`\n"
                "- Validation/test each satisfy configured context-cluster, "
                "spatial-cluster, radial-band, and 2-3 connected-component "
                "constraints. These diagnostics are numeric and do not assign "
                "subjective district labels.",
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in validation.to_dict().items()
                ),
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in artifacts.to_dict().items()
                ),
            ),
            ReportSection(
                "Read-only Verification",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in input_changes.items()
                ),
            ),
            ReportSection(
                "Scope",
                "Only the permanent district assignment, balancing statistics, "
                "and provenance were generated. No 500m scene footprint, "
                "moving window artifact, clipping, relation graph, raster "
                "crop/resampling, tensor, encoder, embedding, model input, or "
                "training cache was created.",
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
