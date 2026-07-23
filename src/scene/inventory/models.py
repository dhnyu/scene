"""Typed records shared by inventory scanners and serializers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    """One source inventory row with nullable kind-specific metadata."""

    run_id: str
    scanned_at_kst: str
    source_name: str
    category: str
    source_kind: str
    source_path: str
    layer_name: str | None = None
    exists: bool = False
    readable: bool = False
    file_size: int | None = None
    modified_time_kst: str | None = None
    sha256: str | None = None
    crs: str | None = None
    geometry_type: str | None = None
    feature_count: int | None = None
    bbox_min_x: float | None = None
    bbox_min_y: float | None = None
    bbox_max_x: float | None = None
    bbox_max_y: float | None = None
    raster_width: int | None = None
    raster_height: int | None = None
    resolution_x: float | None = None
    resolution_y: float | None = None
    extent_min_x: float | None = None
    extent_min_y: float | None = None
    extent_max_x: float | None = None
    extent_max_y: float | None = None
    band_count: int | None = None
    dtype: str | None = None
    nodata: str | None = None
    valid: bool = False
    validation_errors: tuple[str, ...] = ()
    scan_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["validation_errors"] = list(self.validation_errors)
        return value


@dataclass(frozen=True, slots=True)
class InventoryScan:
    """Complete scan outcome, including invalid source records."""

    run_id: str
    started_at_kst: str
    completed_at_kst: str
    duration_seconds: float
    records: tuple[InventoryRecord, ...]

    @property
    def source_count(self) -> int:
        return len(self.records)

    @property
    def failure_count(self) -> int:
        return sum(not record.valid for record in self.records)

    @property
    def valid_count(self) -> int:
        return self.source_count - self.failure_count
