"""M1.4.2 Road Adapter report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.roads.dataset import RoadLinkDataset, RoadNodeDataset
from scene.roads.serialization import RoadArtifactPaths
from scene.roads.validator import RoadValidationResult


def write_road_report(
    links: RoadLinkDataset,
    nodes: RoadNodeDataset,
    validation: RoadValidationResult,
    artifacts: RoadArtifactPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    input_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write the required timestamped Markdown and JSON reports."""

    status = (
        "complete"
        if validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_4_2_road_adapter",
        title="M1.4.2 Road Adapter",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "changed_files": [
                "README.md",
                "docs/contracts/acceptance_tests.md",
                "docs/contracts/implementation_contract.md",
                "src/scene/cli.py",
                "src/scene/roads/",
                "tests/conftest.py",
                "tests/unit/test_road_adapter.py",
                "tests/unit/test_road_cli.py",
                "tests/unit/test_road_reader.py",
                "tests/unit/test_road_serialization.py",
            ],
            "input_stat_changes": list(input_stat_changes),
            "next_step": "M1.4.3 POI Adapter",
            "road_link_feature_count": links.feature_count,
            "road_node_feature_count": nodes.feature_count,
            "status": status,
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                "\n".join(
                    [
                        f"Road Link features: `{links.feature_count}`  ",
                        f"Road Node features: `{nodes.feature_count}`  ",
                        f"Validation: `{'PASS' if validation.valid else 'FAIL'}`  ",
                        f"CRS: `{links.crs}`  ",
                        f"Link geometry: `{links.geometry.geometry_type}`  ",
                        f"Node geometry: `{nodes.geometry.geometry_type}`",
                    ]
                ),
            ),
            ReportSection(
                "Road Mapping",
                "\n".join(
                    [
                        "- Road class: `road_type`",
                        "- Road name: `source_road_name`",
                        "- Road rank: `road_rank`",
                        "- Not declared in the current canonical schema: "
                        "`bridge`, `tunnel`, `direction`",
                        "- No values or columns were inferred for unavailable "
                        "concepts.",
                    ]
                ),
            ),
            ReportSection(
                "Changed Files",
                "\n".join(
                    [
                        "- `README.md`",
                        "- `docs/contracts/acceptance_tests.md`",
                        "- `docs/contracts/implementation_contract.md`",
                        "- `src/scene/cli.py`",
                        "- `src/scene/roads/`",
                        "- `tests/conftest.py`",
                        "- `tests/unit/test_road_adapter.py`",
                        "- `tests/unit/test_road_cli.py`",
                        "- `tests/unit/test_road_reader.py`",
                        "- `tests/unit/test_road_serialization.py`",
                    ]
                ),
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    [
                        "- Link geometry: "
                        f"`{validation.link_geometry.to_dict()}`",
                        "- Node geometry: "
                        f"`{validation.node_geometry.to_dict()}`",
                        "- Canonical schema: "
                        f"`{validation.canonical_schema_valid}`",
                        "- Source metadata: "
                        f"`{validation.source_metadata_valid}`",
                        "- Link-node connected: "
                        f"`{validation.topology_created}`",
                    ]
                ),
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    [
                        f"- Geometry GeoPackage: `{artifacts.geometry_geopackage}`",
                        "- Link attribute Parquet: "
                        f"`{artifacts.link_attribute_parquet}`",
                        "- Node attribute Parquet: "
                        f"`{artifacts.node_attribute_parquet}`",
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
                "Only Road Link and Road Node adapters were created. Geometry "
                "and attributes remain unjoined, and links and nodes remain "
                "unconnected. No POI, raster, stable ID, district split, scene, "
                "clip, observation geometry, graph, relation, tensor, model, or "
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
