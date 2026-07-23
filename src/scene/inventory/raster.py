"""Raster metadata extraction through the read-only GDAL JSON interface."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from scene.inventory.exceptions import MetadataExtractionError


@dataclass(frozen=True, slots=True)
class RasterMetadata:
    crs: str | None
    width: int | None
    height: int | None
    resolution: tuple[float, float] | None
    extent: tuple[float, float, float, float] | None
    band_count: int | None
    dtype: str | None
    nodata: str | None


def _crs_text(coordinate_system: object) -> str | None:
    if not isinstance(coordinate_system, Mapping):
        return None
    identifier = coordinate_system.get("id")
    if isinstance(identifier, Mapping):
        authority = identifier.get("authority")
        code = identifier.get("code")
        if authority and code is not None:
            return f"{authority}:{code}"
    wkt = coordinate_system.get("wkt")
    if isinstance(wkt, str):
        identifiers = re.findall(
            r'\bID\["([^"]+)",\s*([0-9]+)\]',
            wkt,
        )
        if identifiers:
            authority, code = identifiers[-1]
            return f"{authority}:{code}"
    return str(wkt) if wkt else None


def _extent(payload: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    corners = payload.get("cornerCoordinates")
    if isinstance(corners, Mapping):
        points = [
            value
            for key, value in corners.items()
            if key != "center"
            and isinstance(value, list)
            and len(value) >= 2
        ]
        if points:
            x_values = [float(point[0]) for point in points]
            y_values = [float(point[1]) for point in points]
            return (
                min(x_values),
                min(y_values),
                max(x_values),
                max(y_values),
            )
    return None


def _nodata_text(bands: list[object]) -> str | None:
    values = [
        band.get("noDataValue")
        for band in bands
        if isinstance(band, Mapping) and "noDataValue" in band
    ]
    if not values:
        return None
    if all(value == values[0] for value in values):
        return json.dumps(values[0], ensure_ascii=False)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def extract_raster_metadata(path: str | Path) -> RasterMetadata:
    """Run `gdalinfo -json` without statistics or pixel reads."""

    try:
        result = subprocess.run(
            ["gdalinfo", "-json", str(Path(path))],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MetadataExtractionError(
            f"cannot execute gdalinfo for {path!s}: {exc}"
        ) from exc
    if result.returncode != 0:
        message = result.stderr.strip() or "gdalinfo returned a non-zero status"
        raise MetadataExtractionError(
            f"cannot read raster metadata for {path!s}: {message}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MetadataExtractionError(
            f"gdalinfo returned invalid JSON for {path!s}: {exc}"
        ) from exc

    size = payload.get("size")
    width = int(size[0]) if isinstance(size, list) and len(size) >= 2 else None
    height = int(size[1]) if isinstance(size, list) and len(size) >= 2 else None

    transform = payload.get("geoTransform")
    resolution = None
    if isinstance(transform, list) and len(transform) == 6:
        resolution = (
            math.hypot(float(transform[1]), float(transform[2])),
            math.hypot(float(transform[4]), float(transform[5])),
        )

    raw_bands = payload.get("bands")
    bands = raw_bands if isinstance(raw_bands, list) else []
    dtypes = [
        str(band["type"])
        for band in bands
        if isinstance(band, Mapping) and band.get("type")
    ]
    unique_dtypes = list(dict.fromkeys(dtypes))
    dtype = ",".join(unique_dtypes) if unique_dtypes else None

    return RasterMetadata(
        crs=_crs_text(payload.get("coordinateSystem")),
        width=width,
        height=height,
        resolution=resolution,
        extent=_extent(payload),
        band_count=len(bands) if isinstance(raw_bands, list) else None,
        dtype=dtype,
        nodata=_nodata_text(bands),
    )
