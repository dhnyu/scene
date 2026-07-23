"""Typed source-reference raster metadata records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


Extent = tuple[float, float, float, float]
AffineTransform = tuple[float, float, float, float, float, float]
Resolution = tuple[float, float]


@dataclass(frozen=True, slots=True)
class RasterSourceMetadata:
    """Read-only header and file provenance for one registered raster."""

    source_name: str
    category: str
    source_path: str
    exists: bool
    readable: bool
    file_size: int
    modified_time_kst: str
    modified_time_ns: int
    sha256: str
    driver: str
    crs: str | None
    width: int | None
    height: int | None
    resolution: Resolution | None
    extent: Extent | None
    affine_transform: AffineTransform | None
    band_count: int | None
    dtype: str | None
    nodata: str | None
    compression: str | None
    color_table_present: bool
    color_interpretation: str | None
    source_reference_only: bool = True
    pixel_data_read: bool = False
    pixel_data_copied: bool = False
    source_values_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for field in ("resolution", "extent", "affine_transform"):
            item = value[field]
            value[field] = list(item) if item is not None else None
        return value


@dataclass(frozen=True, slots=True)
class GridAlignment:
    """Non-mutating comparison of the Landcover and DEM source grids."""

    same_crs: bool
    same_resolution: bool
    same_origin: bool
    same_extent: bool
    same_grid: bool
    decision_status: str = "D-007 Open"
    resampling_policy_selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RasterMetadataCollection:
    """The two canonical raster references and cross-grid diagnostics."""

    landcover: RasterSourceMetadata
    dem: RasterSourceMetadata
    grid_alignment: GridAlignment

    @property
    def source_count(self) -> int:
        return 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "dem": self.dem.to_dict(),
            "grid_alignment": self.grid_alignment.to_dict(),
            "landcover": self.landcover.to_dict(),
            "source_count": self.source_count,
        }
