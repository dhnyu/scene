"""Human and machine-readable inventory report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.inventory.models import InventoryRecord, InventoryScan
from scene.inventory.serialization import InventoryPaths


def _spatial_summary(record: InventoryRecord) -> str:
    if record.source_kind == "vector":
        bbox = (
            record.bbox_min_x,
            record.bbox_min_y,
            record.bbox_max_x,
            record.bbox_max_y,
        )
        return (
            f"type={record.geometry_type}; features={record.feature_count}; "
            f"bbox={bbox}"
        )
    if record.source_kind == "raster":
        extent = (
            record.extent_min_x,
            record.extent_min_y,
            record.extent_max_x,
            record.extent_max_y,
        )
        return (
            f"size={record.raster_width}x{record.raster_height}; "
            f"resolution=({record.resolution_x},{record.resolution_y}); "
            f"extent={extent}; bands={record.band_count}; "
            f"dtype={record.dtype}; nodata={record.nodata}"
        )
    return "common file metadata only"


def _common_source_table(scan: InventoryScan) -> str:
    rows = [
        "| Source | Path | Exists | Readable | Size | Modified KST | SHA-256 | Valid | Errors |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for record in scan.records:
        errors = "; ".join(record.validation_errors) or "none"
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{record.source_name}`",
                    f"`{record.source_path}`",
                    str(record.exists).lower(),
                    str(record.readable).lower(),
                    str(record.file_size),
                    str(record.modified_time_kst),
                    f"`{record.sha256}`",
                    str(record.valid).lower(),
                    errors.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _spatial_source_table(scan: InventoryScan) -> str:
    rows = [
        "| Source | Category | Kind | CRS | Layer | Spatial metadata |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in scan.records:
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{record.source_name}`",
                    record.category,
                    record.source_kind,
                    str(record.crs),
                    str(record.layer_name),
                    _spatial_summary(record),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def write_inventory_report(
    scan: InventoryScan,
    inventory_paths: InventoryPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    source_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write the required timestamped M1.2 Markdown and JSON reports."""

    source_results = [
        {
            "source_name": record.source_name,
            "valid": record.valid,
            "validation_errors": list(record.validation_errors),
        }
        for record in scan.records
    ]
    return write_reports(
        report_dir,
        f"{scan.run_id}_m1_2_source_inventory",
        title="M1.2 Source Inventory",
        metadata=metadata,
        summary={
            "failure_count": scan.failure_count,
            "inventory_json": str(inventory_paths.json),
            "inventory_parquet": str(inventory_paths.parquet),
            "next_step": "M1.3 Canonical Schema Validation",
            "source_count": scan.source_count,
            "source_stat_changes": list(source_stat_changes),
            "source_results": source_results,
            "records": [record.to_dict() for record in scan.records],
            "status": (
                "complete"
                if scan.failure_count == 0
                else "complete_with_validation_errors"
            ),
            "valid_count": scan.valid_count,
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                f"Registered sources: `{scan.source_count}`  \n"
                f"Valid sources: `{scan.valid_count}`  \n"
                f"Validation failures: `{scan.failure_count}`  \n"
                f"Scan duration: `{scan.duration_seconds:.3f}` seconds",
            ),
            ReportSection("Common Source Metadata", _common_source_table(scan)),
            ReportSection("Spatial Metadata", _spatial_source_table(scan)),
            ReportSection(
                "Read-only Verification",
                "Source size or mtime_ns changes: "
                f"`{len(source_stat_changes)}`"
                + (
                    "\n\n" + "\n".join(
                        f"- `{name}`" for name in source_stat_changes
                    )
                    if source_stat_changes
                    else ""
                ),
            ),
            ReportSection(
                "Artifacts",
                f"- JSON: `{inventory_paths.json}`\n"
                f"- Parquet: `{inventory_paths.parquet}`",
            ),
            ReportSection(
                "Scope",
                "Only source registration, full SHA-256, file metadata, and "
                "vector/raster metadata were produced. No canonical mapping, "
                "scene, clipping, stable ID, tensor, or model artifact was "
                "created.",
            ),
            ReportSection(
                "Milestone Verification",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in (verification or {}).items()
                )
                or "No external milestone verification was supplied.",
            ),
        ),
    )
