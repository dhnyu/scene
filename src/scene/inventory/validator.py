"""Non-throwing validation of completed inventory records."""

from __future__ import annotations

import math
import re
from typing import Iterable

from scene.inventory.models import InventoryRecord


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _finite(values: Iterable[float | None]) -> bool:
    return all(value is not None and math.isfinite(value) for value in values)


def validate_inventory_record(
    record: InventoryRecord,
    extraction_errors: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return all validation errors without raising on source quality."""

    errors = list(extraction_errors)
    if not record.exists:
        errors.append("path_missing")
    if not record.readable:
        errors.append("path_not_readable")
    if record.file_size is None or record.file_size < 0:
        errors.append("file_size_missing")
    if record.modified_time_kst is None:
        errors.append("modified_time_missing")
    if record.sha256 is None or _SHA256.fullmatch(record.sha256) is None:
        errors.append("sha256_missing_or_invalid")

    if record.source_kind == "vector":
        if not record.layer_name:
            errors.append("vector_layer_missing")
        if not record.crs:
            errors.append("vector_crs_missing")
        if not record.geometry_type:
            errors.append("vector_geometry_type_missing")
        if record.feature_count is None or record.feature_count < 0:
            errors.append("vector_feature_count_missing")
        bbox = (
            record.bbox_min_x,
            record.bbox_min_y,
            record.bbox_max_x,
            record.bbox_max_y,
        )
        if not _finite(bbox):
            errors.append("vector_bbox_missing")
        if (
            record.expected_feature_count is not None
            and record.feature_count != record.expected_feature_count
        ):
            errors.append(
                "vector_feature_count_mismatch:"
                f"expected={record.expected_feature_count}:"
                f"actual={record.feature_count}"
            )
        if (
            record.expected_geometry_type is not None
            and record.geometry_type != record.expected_geometry_type
        ):
            errors.append(
                "vector_geometry_type_mismatch:"
                f"expected={record.expected_geometry_type}:"
                f"actual={record.geometry_type}"
            )
        if (
            record.source_crs_declared is not None
            and record.crs != record.source_crs_declared
        ):
            errors.append(
                "vector_crs_mismatch:"
                f"expected={record.source_crs_declared}:actual={record.crs}"
            )
    elif record.source_kind == "raster":
        if not record.crs:
            errors.append("raster_crs_missing")
        if record.raster_width is None or record.raster_width <= 0:
            errors.append("raster_width_missing")
        if record.raster_height is None or record.raster_height <= 0:
            errors.append("raster_height_missing")
        resolution = (record.resolution_x, record.resolution_y)
        if not _finite(resolution) or any(
            value is not None and value <= 0
            for value in resolution
        ):
            errors.append("raster_resolution_missing")
        extent = (
            record.extent_min_x,
            record.extent_min_y,
            record.extent_max_x,
            record.extent_max_y,
        )
        if not _finite(extent):
            errors.append("raster_extent_missing")
        if record.band_count is None or record.band_count <= 0:
            errors.append("raster_band_count_missing")
        if not record.dtype:
            errors.append("raster_dtype_missing")
    elif record.source_kind != "tabular":
        errors.append(f"unsupported_source_kind:{record.source_kind}")

    return tuple(dict.fromkeys(errors))
