"""M1.4.4 Raster Adapter report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.raster.metadata import RasterMetadataCollection, RasterSourceMetadata
from scene.raster.serialize import RasterArtifactPaths
from scene.raster.validator import RasterValidationResult


def _source_summary(source: RasterSourceMetadata) -> str:
    return "\n".join(
        [
            f"- CRS: `{source.crs}`",
            f"- Resolution: `{source.resolution}`",
            f"- Extent: `{source.extent}`",
            f"- Size: `{source.width} x {source.height}`",
            f"- Band count: `{source.band_count}`",
            f"- Dtype: `{source.dtype}`",
            f"- NoData: `{source.nodata}`",
            f"- Compression: `{source.compression}`",
            f"- Affine: `{source.affine_transform}`",
            f"- Source SHA-256: `{source.sha256}`",
            f"- Source size: `{source.file_size}` bytes",
            f"- Source mtime KST: `{source.modified_time_kst}`",
        ]
    )


def write_raster_report(
    collection: RasterMetadataCollection,
    validation: RasterValidationResult,
    artifacts: RasterArtifactPaths,
    report_dir: str | Path,
    metadata: RunMetadata,
    *,
    input_stat_changes: tuple[str, ...] = (),
    verification: Mapping[str, object] | None = None,
) -> ReportPaths:
    """Write timestamped Raster Adapter Markdown and JSON reports."""

    status = (
        "complete"
        if validation.valid and not input_stat_changes
        else "complete_with_validation_errors"
    )
    return write_reports(
        report_dir,
        f"{metadata.run_id}_m1_4_4_raster_adapter",
        title="M1.4.4 Raster Adapter",
        metadata=metadata,
        summary={
            "artifacts": artifacts.to_dict(),
            "changed_files": [
                "README.md",
                "docs/contracts/acceptance_tests.md",
                "docs/contracts/implementation_contract.md",
                "src/scene/cli.py",
                "src/scene/raster/__init__.py",
                "src/scene/raster/exceptions.py",
                "src/scene/raster/metadata.py",
                "src/scene/raster/reader.py",
                "src/scene/raster/reporting.py",
                "src/scene/raster/serialize.py",
                "src/scene/raster/validator.py",
                "src/scene/raster/workflow.py",
                "tests/conftest.py",
                "tests/unit/test_dem_metadata.py",
                "tests/unit/test_landcover_metadata.py",
                "tests/unit/test_raster_cli.py",
                "tests/unit/test_raster_reader.py",
                "tests/unit/test_raster_serialization.py",
            ],
            "collection": collection.to_dict(),
            "input_stat_changes": list(input_stat_changes),
            "source_count": collection.source_count,
            "status": status,
            "validation": validation.to_dict(),
            "verification": dict(verification or {}),
        },
        sections=(
            ReportSection(
                "Summary",
                "### Landcover\n\n"
                f"{_source_summary(collection.landcover)}\n\n"
                "### DEM\n\n"
                f"{_source_summary(collection.dem)}",
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    [
                        f"- Landcover: `{validation.landcover.to_dict()}`",
                        f"- DEM: `{validation.dem.to_dict()}`",
                        "- Grid alignment: "
                        f"`{validation.grid_alignment.to_dict()}`",
                        f"- D-007: `{validation.d007_status}`",
                        f"- D-008: `{validation.d008_status}`",
                        f"- D-009: `{validation.d009_status}`",
                        f"- Issues: `{len(validation.issues)}`",
                    ]
                ),
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    [
                        f"- JSON metadata: `{artifacts.metadata_json}`",
                        f"- Parquet metadata: `{artifacts.metadata_parquet}`",
                    ]
                ),
            ),
            ReportSection(
                "Read-only Verification",
                "\n".join(
                    [
                        "Source size or mtime changes: "
                        f"`{len(input_stat_changes)}`",
                        f"Pixel data read: `{validation.pixel_data_read}`",
                        f"Pixel data copied: `{validation.pixel_data_copied}`",
                        "Source raster copies: "
                        f"`{validation.geotiff_copy_created}`",
                        "Raster values modified: "
                        f"`{validation.raster_values_modified}`",
                    ]
                ),
            ),
            ReportSection(
                "Scope",
                "Only read-only Landcover and DEM source metadata was adapted. "
                "No GeoTIFF or pixel copy, clipping, scene generation, window "
                "extraction, raster tensor, interpolation, resampling, "
                "normalization, reprojection, object-level sampling, encoder, "
                "embedding, model input, or training cache was created.",
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
