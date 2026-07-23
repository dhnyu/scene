"""M1.4.1 Building Adapter report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.buildings.dataset import BuildingDataset
from scene.buildings.serialization import BuildingArtifactPaths
from scene.buildings.validator import BuildingValidationResult
from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata


def write_building_report(
    dataset: BuildingDataset,
    validation: BuildingValidationResult,
    artifacts: BuildingArtifactPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    input_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write the required timestamped Markdown and JSON reports."""

    bbox = dataset.bounding_box
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_4_1_building_adapter",
        title="M1.4.1 Building Adapter",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "attribute_row_count": dataset.attribute_row_count,
            "bbox": list(bbox) if bbox else None,
            "building_feature_count": dataset.feature_count,
            "crs": dataset.crs,
            "geometry_type": dataset.geometry.geometry_type,
            "input_stat_changes": list(input_stat_changes),
            "modalities_joined": False,
            "next_step": "M1.4.2 Road Adapter",
            "observed_area_created": False,
            "stable_id_created": False,
            "status": (
                "complete"
                if validation.valid and not input_stat_changes
                else "complete_with_validation_errors"
            ),
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                f"Building features: `{dataset.feature_count}`  \n"
                f"Attribute rows: `{dataset.attribute_row_count}`  \n"
                f"Validation: `{'PASS' if validation.valid else 'FAIL'}`  \n"
                f"CRS: `{dataset.crs}`  \n"
                f"Geometry type: `{dataset.geometry.geometry_type}`  \n"
                f"Bounding box: `{bbox}`",
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    [
                        f"- Geometry NULL: `{validation.geometry_null_count}`",
                        "- Geometry parse failures: "
                        f"`{validation.geometry_parse_failure_count}`",
                        "- Unexpected geometry type: "
                        f"`{validation.unexpected_geometry_type_count}`",
                        f"- Empty geometry: `{validation.empty_geometry_count}`",
                        "- Canonical schema: "
                        f"`{validation.canonical_schema_valid}`",
                        f"- Source metadata: `{validation.source_metadata_valid}`",
                        f"- Modalities joined: `{not validation.modalities_unjoined}`",
                    ]
                ),
            ),
            ReportSection(
                "Building Mapping",
                "\n".join(
                    [
                        "- `A9 -> building_use`",
                        "- `A11 -> building_structure`",
                        "- `A16 -> building_height_m`",
                        "- `A12 -> source_building_area_m2` is provenance only.",
                        "- Observed building area was not created.",
                    ]
                ),
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    [
                        f"- Geometry GeoPackage: `{artifacts.geometry_geopackage}`",
                        f"- Attribute Parquet: `{artifacts.attribute_parquet}`",
                        f"- JSON metadata: `{artifacts.metadata_json}`",
                    ]
                ),
            ),
            ReportSection(
                "Read-only Verification",
                f"Input size or mtime changes: `{len(input_stat_changes)}`",
            ),
            ReportSection(
                "Scope",
                "Only the building modality was adapted. Geometry and attributes "
                "remain unjoined. No road, POI, raster adapter, stable ID, "
                "district split, scene, clipping, observation geometry, "
                "relation, tensor, model, or training cache was created.",
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
