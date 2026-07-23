"""Read registered raster headers without reading pixel arrays."""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scene.core.config import ProjectConfig, SourceConfig
from scene.inventory.hashing import sha256_file
from scene.raster.exceptions import RasterReaderError
from scene.raster.metadata import RasterSourceMetadata


_RASTER_NAMES = ("seoul_landcover", "seoul_dem")


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RasterReaderError(f"{context} must be a mapping")
    return value


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
    return None


def _extent(payload: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    corners = payload.get("cornerCoordinates")
    if not isinstance(corners, Mapping):
        return None
    points = [
        value
        for key, value in corners.items()
        if key != "center"
        and isinstance(value, list)
        and len(value) >= 2
    ]
    if not points:
        return None
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    return (
        min(x_values),
        min(y_values),
        max(x_values),
        max(y_values),
    )


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


def _run_gdalinfo(path: Path) -> Mapping[str, Any]:
    try:
        result = subprocess.run(
            ["gdalinfo", "-json", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RasterReaderError(
            f"cannot execute gdalinfo for {path}: {exc}"
        ) from exc
    if result.returncode != 0:
        message = result.stderr.strip() or "gdalinfo returned non-zero status"
        raise RasterReaderError(
            f"cannot read raster metadata for {path}: {message}"
        )
    try:
        return _mapping(json.loads(result.stdout), "gdalinfo JSON")
    except json.JSONDecodeError as exc:
        raise RasterReaderError(
            f"gdalinfo returned invalid JSON for {path}: {exc}"
        ) from exc


def _source_map(config: ProjectConfig) -> dict[str, SourceConfig]:
    sources = {
        source.source_name: source
        for source in config.sources
        if source.source_name in _RASTER_NAMES
    }
    missing = set(_RASTER_NAMES) - set(sources)
    if missing:
        raise RasterReaderError(
            f"registered raster sources missing: {sorted(missing)}"
        )
    for name, source in sources.items():
        if source.kind != "raster":
            raise RasterReaderError(f"{name} must be registered as raster")
    return sources


class RasterReader:
    """Extract metadata for the two registered read-only raster sources."""

    def read(self, config: ProjectConfig) -> tuple[RasterSourceMetadata, ...]:
        sources = _source_map(config)
        return tuple(
            self._read_source(sources[name], config.timezone)
            for name in _RASTER_NAMES
        )

    def _read_source(
        self,
        source: SourceConfig,
        timezone_name: str,
    ) -> RasterSourceMetadata:
        path = source.path
        if not path.is_file():
            raise RasterReaderError(f"raster source does not exist: {path}")
        if not os.access(path, os.R_OK):
            raise RasterReaderError(f"raster source is not readable: {path}")
        try:
            stat = path.stat()
        except OSError as exc:
            raise RasterReaderError(f"cannot stat raster source {path}: {exc}") from exc
        payload = _run_gdalinfo(path)
        size = payload.get("size")
        width = (
            int(size[0])
            if isinstance(size, list) and len(size) >= 2
            else None
        )
        height = (
            int(size[1])
            if isinstance(size, list) and len(size) >= 2
            else None
        )
        raw_transform = payload.get("geoTransform")
        transform = (
            tuple(float(value) for value in raw_transform)
            if isinstance(raw_transform, list) and len(raw_transform) == 6
            else None
        )
        resolution = (
            (
                math.hypot(transform[1], transform[2]),
                math.hypot(transform[4], transform[5]),
            )
            if transform is not None
            else None
        )
        raw_bands = payload.get("bands")
        bands = raw_bands if isinstance(raw_bands, list) else []
        dtypes = [
            str(band["type"])
            for band in bands
            if isinstance(band, Mapping) and band.get("type")
        ]
        unique_dtypes = list(dict.fromkeys(dtypes))
        image_structure = _mapping(
            _mapping(payload.get("metadata", {}), "raster metadata").get(
                "IMAGE_STRUCTURE", {}
            ),
            "IMAGE_STRUCTURE metadata",
        )
        first_band = (
            bands[0] if bands and isinstance(bands[0], Mapping) else {}
        )
        modified_time = datetime.fromtimestamp(
            stat.st_mtime,
            ZoneInfo(timezone_name),
        ).isoformat(timespec="seconds")
        return RasterSourceMetadata(
            source_name=source.source_name,
            category=source.category,
            source_path=str(path),
            exists=True,
            readable=True,
            file_size=stat.st_size,
            modified_time_kst=modified_time,
            modified_time_ns=stat.st_mtime_ns,
            sha256=sha256_file(path),
            driver=str(payload.get("driverShortName") or ""),
            crs=_crs_text(payload.get("coordinateSystem")),
            width=width,
            height=height,
            resolution=resolution,
            extent=_extent(payload),
            affine_transform=transform,
            band_count=len(bands) if isinstance(raw_bands, list) else None,
            dtype=",".join(unique_dtypes) if unique_dtypes else None,
            nodata=_nodata_text(bands),
            compression=(
                str(image_structure["COMPRESSION"])
                if image_structure.get("COMPRESSION") is not None
                else None
            ),
            color_table_present=bool(
                isinstance(first_band, Mapping)
                and "colorTable" in first_band
            ),
            color_interpretation=(
                str(first_band["colorInterpretation"])
                if isinstance(first_band, Mapping)
                and first_band.get("colorInterpretation")
                else None
            ),
        )
