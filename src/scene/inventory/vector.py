"""Vector metadata extraction without loading feature attributes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyogrio

from scene.inventory.exceptions import MetadataExtractionError


@dataclass(frozen=True, slots=True)
class VectorMetadata:
    crs: str | None
    geometry_type: str | None
    feature_count: int | None
    bbox: tuple[float, float, float, float] | None
    layer_name: str | None
    field_names: tuple[str, ...]


def extract_vector_metadata(
    path: str | Path,
    layer: str,
) -> VectorMetadata:
    """Read OGR layer metadata and force count/bounds when the driver needs it."""

    try:
        info = pyogrio.read_info(
            Path(path),
            layer=layer,
            force_feature_count=True,
            force_total_bounds=True,
        )
    except Exception as exc:
        raise MetadataExtractionError(
            f"cannot read vector metadata for {path!s}:{layer}: {exc}"
        ) from exc

    raw_bounds = info.get("total_bounds")
    bounds = (
        tuple(float(value) for value in raw_bounds)
        if raw_bounds is not None
        else None
    )
    raw_count = info.get("features")
    feature_count = (
        int(raw_count)
        if raw_count is not None and int(raw_count) >= 0
        else None
    )
    crs = info.get("crs")
    geometry_type = info.get("geometry_type")
    layer_name = info.get("layer_name")
    return VectorMetadata(
        crs=str(crs) if crs else None,
        geometry_type=str(geometry_type) if geometry_type else None,
        feature_count=feature_count,
        bbox=bounds,
        layer_name=str(layer_name) if layer_name else layer,
        field_names=tuple(str(field) for field in info.get("fields", ())),
    )
