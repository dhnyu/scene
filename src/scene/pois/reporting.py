"""M1.4.3 POI Adapter report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.pois.category import (
    CATEGORY_COLUMNS,
    CATEGORY_PATH_NULL_TOKEN,
    CATEGORY_PATH_SEPARATOR,
)
from scene.pois.dataset import POIDataset
from scene.pois.serialization import POIArtifactPaths
from scene.pois.validator import POIValidationResult


def write_poi_report(
    dataset: POIDataset,
    validation: POIValidationResult,
    artifacts: POIArtifactPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    input_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write the required timestamped Markdown and JSON reports."""

    join = validation.join_key
    status = (
        "complete"
        if validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_4_3_poi_adapter",
        title="M1.4.3 POI Adapter",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "attribute_row_count": dataset.attribute_row_count,
            "category_path_validation": (
                "PASS" if validation.category_path_valid else "FAIL"
            ),
            "changed_files": [
                "README.md",
                "docs/contracts/acceptance_tests.md",
                "docs/contracts/implementation_contract.md",
                "src/scene/cli.py",
                "src/scene/pois/",
                "tests/conftest.py",
                "tests/unit/test_poi_adapter.py",
                "tests/unit/test_poi_category.py",
                "tests/unit/test_poi_cli.py",
                "tests/unit/test_poi_reader.py",
                "tests/unit/test_poi_serialization.py",
            ],
            "input_stat_changes": list(input_stat_changes),
            "join_key_validation": "PASS" if join.valid else "FAIL",
            "next_step": "M1.4.4 Raster Adapter",
            "poi_geometry_feature_count": dataset.feature_count,
            "status": status,
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                "\n".join(
                    [
                        f"POI geometry features: `{dataset.feature_count}`  ",
                        f"POI attribute rows: `{dataset.attribute_row_count}`  ",
                        f"Validation: `{'PASS' if validation.valid else 'FAIL'}`  ",
                        f"CRS: `{dataset.crs}`  ",
                        f"Geometry type: `{dataset.geometry.geometry_type}`",
                    ]
                ),
            ),
            ReportSection(
                "Join Key Diagnostics",
                "\n".join(
                    [
                        "- Canonical key: `source_poi_id` "
                        "(source `NF_ID` on both frames)",
                        f"- Geometry unique keys: `{join.geometry_unique_key_count}`",
                        f"- Attribute unique keys: `{join.attribute_unique_key_count}`",
                        f"- Geometry NULL keys: `{join.geometry_null_key_count}`",
                        f"- Attribute NULL keys: `{join.attribute_null_key_count}`",
                        f"- Geometry-only keys: `{join.geometry_only_key_count}`",
                        f"- Attribute-only keys: `{join.attribute_only_key_count}`",
                        "- Geometry duplicate keys/rows: "
                        f"`{join.geometry_duplicate_key_count}` / "
                        f"`{join.geometry_duplicate_row_count}`",
                        "- Attribute duplicate keys/rows: "
                        f"`{join.attribute_duplicate_key_count}` / "
                        f"`{join.attribute_duplicate_row_count}`",
                        f"- Cardinality: `{join.cardinality}`",
                        f"- Validation: `{'PASS' if join.valid else 'FAIL'}`",
                    ]
                ),
            ),
            ReportSection(
                "Category Hierarchy And Path",
                "\n".join(
                    [
                        f"- Source columns: `{list(CATEGORY_COLUMNS)}`",
                        "- Normalization: `identity`",
                        f"- Separator: `{CATEGORY_PATH_SEPARATOR}`",
                        f"- Source NULL token: `{CATEGORY_PATH_NULL_TOKEN}`",
                        "- Category NULL counts: "
                        f"`{list(validation.category_null_counts)}`",
                        "- Category empty counts: "
                        f"`{list(validation.category_empty_counts)}`",
                        "- Hierarchy fields: "
                        f"`{'PASS' if validation.category_hierarchy_valid else 'FAIL'}`",
                        "- Path validation: "
                        f"`{'PASS' if validation.category_path_valid else 'FAIL'}`",
                        "- Source labels preserved: "
                        f"`{validation.source_labels_preserved}`",
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
                "Only the POI adapter was created. Geometry and attributes "
                "remain unjoined and source rows were not deduplicated or "
                "merged. No raster adapter, stable ID, district split, scene, "
                "clip, observation geometry, polygonization, geometry "
                "embedding, relation, tensor, model, or training cache was "
                "created.",
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
