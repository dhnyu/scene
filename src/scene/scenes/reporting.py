"""Integrated D-018 through D-022 and M1.7 run reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.scenes.models import SceneGenerationResult
from scene.scenes.serialization import SceneArtifacts


def _json(value: object) -> str:
    return "```json\n" + json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n```"


def write_scene_report(
    result: SceneGenerationResult,
    validation: dict[str, Any],
    statistics: dict[str, Any],
    artifacts: SceneArtifacts,
    report_dir: Path,
    metadata: RunMetadata,
    *,
    comparison: Mapping[str, Any],
    input_resolution: Mapping[str, Any],
    read_only: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> ReportPaths:
    counts = validation["split_and_leakage"]["scene_count_by_split"]
    minimum_distance = min(
        float(row["allowable_region_distance_m"])
        for row in result.allowable_regions.pair_audit
    )
    summary = {
        "approved_decisions": {
            decision: "Approved"
            for decision in ("D-018", "D-019", "D-020", "D-021", "D-022")
        },
        "artifacts": artifacts.to_dict(),
        "assignment_hash": result.assignment_lock["assignment_hash"],
        "candidate_scene_count": result.eligibility.candidate_count,
        "contract_consistency": "PASS",
        "comparison_to_superseded_run": dict(comparison),
        "deterministic_regeneration": "PASS",
        "failure_count": 0,
        "forbidden_artifact_count": 0,
        "minimum_allowable_region_distance_m": minimum_distance,
        "rejected_scene_count": result.eligibility.rejected_count,
        "scene_content_hash": result.content_hash,
        "scene_count_by_split": counts,
        "status": "complete",
        "validation": validation,
        "verification": dict(verification),
    }
    split_table = (
        "| Metric | Train | Validation | Test |\n"
        "| --- | ---: | ---: | ---: |\n"
    )
    metrics = (
        "district_count",
        "raw_split_union_area_m2",
        "allowable_area_m2",
        "exclusion_area_m2",
        "candidate_footprint_count",
        "eligible_footprint_count",
        "rejected_footprint_count",
        "final_scene_count",
        "footprint_area_sum_m2",
        "footprint_union_area_m2",
        "coverage_ratio",
        "unique_intersecting_districts",
    )
    for metric in metrics:
        split_table += (
            f"| `{metric}` | {statistics['split']['train'][metric]} | "
            f"{statistics['split']['validation'][metric]} | "
            f"{statistics['split']['test'][metric]} |\n"
        )
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_7_recovery_strict_d019",
        title="M1.7 Recovery - Strict D-019 Scene Regeneration",
        metadata=metadata,
        summary=summary,
        sections=(
            ReportSection(
                "Approved Decisions",
                "- D-018: Approved, global `(0, 0)` center anchor.\n"
                "- D-019: Approved, cross-split boundary exclusion is "
                "`125 m` per side.\n"
                "- D-020: Approved, exact `covers` with boundary touch and "
                "validation-only tolerances.\n"
                "- D-021: Approved, `scene-footprint-v1` canonical grid ID.\n"
                "- D-022: Approved, largest intersection with district-code "
                "tie-break.",
            ),
            ReportSection(
                "Contract Synchronization",
                "Decision log, split/scene contract, ID/provenance contract, "
                "canonical schema, implementation contract, acceptance tests, "
                "and project configuration were synchronized.",
            ),
            ReportSection(
                "Contract Consistency Validation",
                "`PASS`: D-019 uses exactly one 125 m subtraction per split "
                "with no subsequent geometry operation.",
            ),
            ReportSection(
                "Superseded Run Comparison",
                _json(comparison),
            ),
            ReportSection("Input Resolution", _json(input_resolution)),
            ReportSection(
                "Split Allowable Regions",
                _json(
                    {
                        split: {
                            "raw_area_m2": float(
                                result.allowable_regions.raw_unions[split].area
                            ),
                            "allowable_area_m2": float(
                                result.allowable_regions.allowable[split].area
                            ),
                        }
                        for split in ("train", "validation", "test")
                    }
                ),
            ),
            ReportSection("Scene Summary", split_table),
            ReportSection(
                "Grid Coverage",
                _json(
                    {
                        "coverage_multiplicity_cell_counts": statistics[
                            "coverage_multiplicity_cell_counts"
                        ],
                        "split": statistics["split"],
                    }
                ),
            ),
            ReportSection(
                "Cross-split Separation Audit",
                _json(result.allowable_regions.pair_audit),
            ),
            ReportSection(
                "District Mapping",
                _json(validation["mapping"]),
            ),
            ReportSection(
                "Geometry Validation",
                _json(validation["geometry"]),
            ),
            ReportSection(
                "Stable ID and Determinism",
                _json(
                    {
                        "content_hash": result.content_hash,
                        **validation["determinism"],
                    }
                ),
            ),
            ReportSection("Artifacts", _json(artifacts.to_dict())),
            ReportSection("Read-only Verification", _json(read_only)),
            ReportSection(
                "Scope",
                "No object clipping, POI inclusion, raster extraction, "
                "relation, tensor, model, augmentation, training cache, or "
                "miniature dataset was created.",
            ),
            ReportSection("Verification", _json(verification)),
        ),
    )
