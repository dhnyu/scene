"""Read-only source scanner that preserves one record per registry entry."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from scene.inventory.hashing import sha256_file
from scene.inventory.models import InventoryRecord, InventoryScan
from scene.inventory.raster import extract_raster_metadata
from scene.inventory.registry import SourceDescriptor, SourceRegistry
from scene.inventory.validator import validate_inventory_record
from scene.inventory.vector import extract_vector_metadata


KST = ZoneInfo("Asia/Seoul")


def _readable_file(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False


def _scan_source(
    source: SourceDescriptor,
    *,
    run_id: str,
    scanned_at_kst: str,
) -> InventoryRecord:
    started = time.monotonic()
    errors: list[str] = []
    path = source.path

    try:
        exists = path.exists()
        is_file = path.is_file()
    except OSError as exc:
        exists = False
        is_file = False
        errors.append(f"path_stat_error:{type(exc).__name__}:{exc}")

    file_size = None
    modified_time = None
    if exists:
        try:
            stat = path.stat()
            file_size = stat.st_size if is_file else None
            modified_time = datetime.fromtimestamp(
                stat.st_mtime,
                tz=KST,
            ).isoformat(timespec="seconds")
        except OSError as exc:
            errors.append(f"path_stat_error:{type(exc).__name__}:{exc}")

    readable = is_file and _readable_file(path)
    digest = None
    if readable:
        try:
            digest = sha256_file(path)
        except Exception as exc:
            errors.append(f"sha256_error:{type(exc).__name__}:{exc}")

    record = InventoryRecord(
        run_id=run_id,
        scanned_at_kst=scanned_at_kst,
        source_name=source.source_name,
        category=source.category,
        source_kind=source.kind,
        source_path=str(path),
        layer_name=source.layer,
        exists=exists,
        readable=readable,
        file_size=file_size,
        modified_time_kst=modified_time,
        sha256=digest,
    )

    if readable and source.kind == "vector" and source.layer is not None:
        try:
            metadata = extract_vector_metadata(path, source.layer)
            bbox = metadata.bbox or (None, None, None, None)
            record = replace(
                record,
                crs=metadata.crs,
                geometry_type=metadata.geometry_type,
                feature_count=metadata.feature_count,
                bbox_min_x=bbox[0],
                bbox_min_y=bbox[1],
                bbox_max_x=bbox[2],
                bbox_max_y=bbox[3],
                layer_name=metadata.layer_name,
            )
        except Exception as exc:
            errors.append(
                f"vector_metadata_error:{type(exc).__name__}:{exc}"
            )
    elif readable and source.kind == "raster":
        try:
            metadata = extract_raster_metadata(path)
            resolution = metadata.resolution or (None, None)
            extent = metadata.extent or (None, None, None, None)
            record = replace(
                record,
                crs=metadata.crs,
                raster_width=metadata.width,
                raster_height=metadata.height,
                resolution_x=resolution[0],
                resolution_y=resolution[1],
                extent_min_x=extent[0],
                extent_min_y=extent[1],
                extent_max_x=extent[2],
                extent_max_y=extent[3],
                band_count=metadata.band_count,
                dtype=metadata.dtype,
                nodata=metadata.nodata,
            )
        except Exception as exc:
            errors.append(
                f"raster_metadata_error:{type(exc).__name__}:{exc}"
            )

    validation_errors = validate_inventory_record(record, errors)
    return replace(
        record,
        valid=not validation_errors,
        validation_errors=validation_errors,
        scan_duration_seconds=time.monotonic() - started,
    )


def scan_inventory(
    registry: SourceRegistry,
    *,
    run_id: str,
    started_at_kst: str,
    logger: logging.Logger | None = None,
) -> InventoryScan:
    """Scan every registered source even when earlier records are invalid."""

    started = time.monotonic()
    records: list[InventoryRecord] = []
    for source in registry:
        if logger:
            logger.info("source scan started: %s", source.source_name)
        record = _scan_source(
            source,
            run_id=run_id,
            scanned_at_kst=started_at_kst,
        )
        records.append(record)
        if logger:
            logger.info(
                "source scan completed: %s valid=%s errors=%d",
                source.source_name,
                record.valid,
                len(record.validation_errors),
            )
    completed = datetime.now(tz=KST).isoformat(timespec="seconds")
    return InventoryScan(
        run_id=run_id,
        started_at_kst=started_at_kst,
        completed_at_kst=completed,
        duration_seconds=time.monotonic() - started,
        records=tuple(records),
    )
