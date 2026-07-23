"""Canonical JSON and Zstandard Parquet inventory serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.inventory.exceptions import InventorySerializationError
from scene.inventory.models import InventoryScan


INVENTORY_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("scanned_at_kst", pa.string(), nullable=False),
        pa.field("source_name", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("source_kind", pa.string(), nullable=False),
        pa.field("source_path", pa.string(), nullable=False),
        pa.field("layer_name", pa.string()),
        pa.field("source_format", pa.string()),
        pa.field("source_crs_declared", pa.string()),
        pa.field("administrative_level", pa.string()),
        pa.field("geographic_scope", pa.string()),
        pa.field("expected_geometry_type", pa.string()),
        pa.field("expected_feature_count", pa.int64()),
        pa.field("read_only", pa.bool_(), nullable=False),
        pa.field("canonical_adapter", pa.string()),
        pa.field("config_hash", pa.string()),
        pa.field("field_names", pa.list_(pa.string()), nullable=False),
        pa.field("exists", pa.bool_(), nullable=False),
        pa.field("readable", pa.bool_(), nullable=False),
        pa.field("file_size", pa.int64()),
        pa.field("modified_time_kst", pa.string()),
        pa.field("sha256", pa.string()),
        pa.field("crs", pa.string()),
        pa.field("geometry_type", pa.string()),
        pa.field("feature_count", pa.int64()),
        pa.field("bbox_min_x", pa.float64()),
        pa.field("bbox_min_y", pa.float64()),
        pa.field("bbox_max_x", pa.float64()),
        pa.field("bbox_max_y", pa.float64()),
        pa.field("raster_width", pa.int64()),
        pa.field("raster_height", pa.int64()),
        pa.field("resolution_x", pa.float64()),
        pa.field("resolution_y", pa.float64()),
        pa.field("extent_min_x", pa.float64()),
        pa.field("extent_min_y", pa.float64()),
        pa.field("extent_max_x", pa.float64()),
        pa.field("extent_max_y", pa.float64()),
        pa.field("band_count", pa.int32()),
        pa.field("dtype", pa.string()),
        pa.field("nodata", pa.string()),
        pa.field("valid", pa.bool_(), nullable=False),
        pa.field(
            "validation_errors",
            pa.list_(pa.string()),
            nullable=False,
        ),
        pa.field("scan_duration_seconds", pa.float64(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class InventoryPaths:
    json: Path
    parquet: Path


def _write_json(scan: InventoryScan, path: Path) -> None:
    payload = {
        "completed_at_kst": scan.completed_at_kst,
        "duration_seconds": scan.duration_seconds,
        "failure_count": scan.failure_count,
        "inventory_schema_version": "1.1",
        "records": [record.to_dict() for record in scan.records],
        "run_id": scan.run_id,
        "source_count": scan.source_count,
        "started_at_kst": scan.started_at_kst,
        "valid_count": scan.valid_count,
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_parquet(scan: InventoryScan, path: Path) -> None:
    rows = [record.to_dict() for record in scan.records]
    table = pa.Table.from_pylist(rows, schema=INVENTORY_SCHEMA)
    temporary = path.with_name(f".{path.name}.tmp")
    pq.write_table(
        table,
        temporary,
        compression="zstd",
        version="2.6",
    )
    temporary.replace(path)


def write_inventory(
    scan: InventoryScan,
    directory: str | Path,
) -> InventoryPaths:
    """Write matching inventory records without dropping invalid sources."""

    output_dir = Path(directory)
    json_path = output_dir / f"{scan.run_id}_source_inventory.json"
    parquet_path = output_dir / f"{scan.run_id}_source_inventory.parquet"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(scan, json_path)
        _write_parquet(scan, parquet_path)
    except (OSError, TypeError, ValueError, pa.ArrowException) as exc:
        raise InventorySerializationError(
            f"cannot serialize source inventory: {exc}"
        ) from exc
    return InventoryPaths(json=json_path, parquet=parquet_path)
