"""Validate raster references and diagnose source-grid alignment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from scene.raster.metadata import (
    Extent,
    GridAlignment,
    RasterSourceMetadata,
)


@dataclass(frozen=True, slots=True)
class RasterValidationIssue:
    """One Raster Adapter contract violation."""

    code: str
    message: str
    source_name: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RasterSourceValidation:
    """Validation flags for one source-reference raster."""

    source_name: str
    metadata_complete: bool
    provenance_valid: bool
    crs_valid: bool
    dimensions_valid: bool
    resolution_valid: bool
    extent_valid: bool
    affine_valid: bool
    north_up: bool
    geometry_alignment_valid: bool
    band_count_valid: bool
    dtype_valid: bool
    nodata_valid: bool
    reference_only_valid: bool

    @property
    def valid(self) -> bool:
        return all(
            (
                self.metadata_complete,
                self.provenance_valid,
                self.crs_valid,
                self.dimensions_valid,
                self.resolution_valid,
                self.extent_valid,
                self.affine_valid,
                self.north_up,
                self.geometry_alignment_valid,
                self.band_count_valid,
                self.dtype_valid,
                self.nodata_valid,
                self.reference_only_valid,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["valid"] = self.valid
        return value


@dataclass(frozen=True, slots=True)
class RasterValidationResult:
    """Complete M1.4.4 source and cross-grid validation."""

    landcover: RasterSourceValidation
    dem: RasterSourceValidation
    grid_alignment: GridAlignment
    d007_status: str
    d008_status: str
    d009_status: str
    pixel_data_read: bool
    pixel_data_copied: bool
    geotiff_copy_created: bool
    raster_values_modified: bool
    resampling_policy_selected: bool
    issues: tuple[RasterValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return (
            self.landcover.valid
            and self.dem.valid
            and not self.pixel_data_read
            and not self.pixel_data_copied
            and not self.geotiff_copy_created
            and not self.raster_values_modified
            and not self.resampling_policy_selected
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "d007_status": self.d007_status,
            "d008_status": self.d008_status,
            "d009_status": self.d009_status,
            "dem": self.dem.to_dict(),
            "geotiff_copy_created": self.geotiff_copy_created,
            "grid_alignment": self.grid_alignment.to_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
            "landcover": self.landcover.to_dict(),
            "pixel_data_copied": self.pixel_data_copied,
            "pixel_data_read": self.pixel_data_read,
            "raster_values_modified": self.raster_values_modified,
            "resampling_policy_selected": self.resampling_policy_selected,
            "valid": self.valid,
        }


def _close(left: float, right: float, tolerance: float = 1e-7) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)


def _sequence_close(
    left: tuple[float, ...] | None,
    right: tuple[float, ...] | None,
) -> bool:
    return (
        left is not None
        and right is not None
        and len(left) == len(right)
        and all(_close(a, b) for a, b in zip(left, right, strict=True))
    )


def _extent_from_affine(source: RasterSourceMetadata) -> Extent | None:
    transform = source.affine_transform
    if transform is None or source.width is None or source.height is None:
        return None
    x0, a, b, y0, d, e = transform
    corners = (
        (x0, y0),
        (x0 + a * source.width, y0 + d * source.width),
        (x0 + b * source.height, y0 + e * source.height),
        (
            x0 + a * source.width + b * source.height,
            y0 + d * source.width + e * source.height,
        ),
    )
    x_values = [point[0] for point in corners]
    y_values = [point[1] for point in corners]
    return (
        min(x_values),
        min(y_values),
        max(x_values),
        max(y_values),
    )


def _grid_alignment(
    landcover: RasterSourceMetadata,
    dem: RasterSourceMetadata,
) -> GridAlignment:
    same_crs = landcover.crs is not None and landcover.crs == dem.crs
    same_resolution = _sequence_close(
        landcover.resolution,
        dem.resolution,
    )
    landcover_origin = (
        (landcover.affine_transform[0], landcover.affine_transform[3])
        if landcover.affine_transform is not None
        else None
    )
    dem_origin = (
        (dem.affine_transform[0], dem.affine_transform[3])
        if dem.affine_transform is not None
        else None
    )
    same_origin = _sequence_close(landcover_origin, dem_origin)
    same_extent = _sequence_close(landcover.extent, dem.extent)
    return GridAlignment(
        same_crs=same_crs,
        same_resolution=same_resolution,
        same_origin=same_origin,
        same_extent=same_extent,
        same_grid=(
            same_crs
            and same_resolution
            and same_origin
            and same_extent
        ),
    )


def _validate_source(
    source: RasterSourceMetadata,
    issues: list[RasterValidationIssue],
) -> RasterSourceValidation:
    metadata_complete = all(
        value is not None
        for value in (
            source.crs,
            source.width,
            source.height,
            source.resolution,
            source.extent,
            source.affine_transform,
            source.band_count,
            source.dtype,
            source.nodata,
        )
    )
    provenance_valid = (
        source.exists
        and source.readable
        and source.file_size > 0
        and source.modified_time_ns > 0
        and len(source.sha256) == 64
    )
    crs_valid = source.crs == "EPSG:5186"
    dimensions_valid = (
        source.width is not None
        and source.height is not None
        and source.width > 0
        and source.height > 0
    )
    resolution_valid = (
        source.resolution is not None
        and all(math.isfinite(value) and value > 0 for value in source.resolution)
    )
    extent_valid = (
        source.extent is not None
        and all(math.isfinite(value) for value in source.extent)
        and source.extent[0] < source.extent[2]
        and source.extent[1] < source.extent[3]
    )
    affine_valid = (
        source.affine_transform is not None
        and all(math.isfinite(value) for value in source.affine_transform)
    )
    north_up = (
        source.affine_transform is not None
        and source.affine_transform[1] > 0
        and _close(source.affine_transform[2], 0.0)
        and _close(source.affine_transform[4], 0.0)
        and source.affine_transform[5] < 0
    )
    geometry_alignment_valid = _sequence_close(
        source.extent,
        _extent_from_affine(source),
    )
    band_count_valid = source.band_count == 1
    dtype_valid = bool(source.dtype)
    nodata_valid = source.nodata is not None
    reference_only_valid = (
        source.source_reference_only
        and not source.pixel_data_read
        and not source.pixel_data_copied
        and not source.source_values_modified
    )
    checks = {
        "metadata_incomplete": metadata_complete,
        "source_provenance_invalid": provenance_valid,
        "crs_invalid": crs_valid,
        "dimensions_invalid": dimensions_valid,
        "resolution_invalid": resolution_valid,
        "extent_invalid": extent_valid,
        "affine_invalid": affine_valid,
        "not_north_up": north_up,
        "geometry_alignment_invalid": geometry_alignment_valid,
        "band_count_invalid": band_count_valid,
        "dtype_invalid": dtype_valid,
        "nodata_missing": nodata_valid,
        "reference_only_violation": reference_only_valid,
    }
    for code, passed in checks.items():
        if not passed:
            issues.append(
                RasterValidationIssue(
                    code=code,
                    message=f"{source.source_name} failed {code}",
                    source_name=source.source_name,
                )
            )
    return RasterSourceValidation(
        source_name=source.source_name,
        metadata_complete=metadata_complete,
        provenance_valid=provenance_valid,
        crs_valid=crs_valid,
        dimensions_valid=dimensions_valid,
        resolution_valid=resolution_valid,
        extent_valid=extent_valid,
        affine_valid=affine_valid,
        north_up=north_up,
        geometry_alignment_valid=geometry_alignment_valid,
        band_count_valid=band_count_valid,
        dtype_valid=dtype_valid,
        nodata_valid=nodata_valid,
        reference_only_valid=reference_only_valid,
    )


class RasterValidator:
    """Validate two source raster references without selecting a target grid."""

    def validate(
        self,
        landcover: RasterSourceMetadata,
        dem: RasterSourceMetadata,
    ) -> RasterValidationResult:
        issues: list[RasterValidationIssue] = []
        if landcover.source_name != "seoul_landcover":
            issues.append(
                RasterValidationIssue(
                    code="landcover_source_mismatch",
                    message="expected seoul_landcover",
                    source_name=landcover.source_name,
                )
            )
        if dem.source_name != "seoul_dem":
            issues.append(
                RasterValidationIssue(
                    code="dem_source_mismatch",
                    message="expected seoul_dem",
                    source_name=dem.source_name,
                )
            )
        landcover_result = _validate_source(landcover, issues)
        dem_result = _validate_source(dem, issues)
        alignment = _grid_alignment(landcover, dem)
        return RasterValidationResult(
            landcover=landcover_result,
            dem=dem_result,
            grid_alignment=alignment,
            d007_status="Open: no target grid or resampling policy selected",
            d008_status="Open: no semantic landcover labels assigned",
            d009_status="Open: DEM unit and vertical datum unresolved",
            pixel_data_read=False,
            pixel_data_copied=False,
            geotiff_copy_created=False,
            raster_values_modified=False,
            resampling_policy_selected=False,
            issues=tuple(issues),
        )
