"""Typed boundary audit and validation records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import geopandas as gpd


@dataclass(frozen=True, slots=True)
class LayerAudit:
    layer_name: str
    row_count: int
    crs: str | None
    geometry_type: str | None
    fields: tuple[str, ...]
    bbox: tuple[float, float, float, float] | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fields"] = list(self.fields)
        value["bbox"] = list(self.bbox) if self.bbox is not None else None
        return value


@dataclass(frozen=True, slots=True)
class BoundarySourceAudit:
    source_path: str
    exists: bool
    readable: bool
    file_size: int
    modified_time_ns: int
    sha256: str
    layers: tuple[LayerAudit, ...]
    district_layer: str
    sido_layer: str
    district_code_field: str
    district_name_field: str
    sido_code_field: str
    sido_name_field: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["layers"] = [layer.to_dict() for layer in self.layers]
        return value


@dataclass(frozen=True, slots=True)
class CanonicalDistricts:
    districts: gpd.GeoDataFrame
    seoul: gpd.GeoDataFrame
    source_audit: BoundarySourceAudit
    content_hash: str


@dataclass(frozen=True, slots=True)
class BoundaryValidation:
    row_count: int
    geometry_null: int
    geometry_empty: int
    geometry_invalid: int
    district_code_null: int
    district_code_duplicate: int
    district_name_null: int
    district_name_duplicate: int
    district_id_null: int
    district_id_duplicate: int
    outside_seoul_code: int
    crs: str | None
    geometry_types: tuple[str, ...]
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["geometry_types"] = list(self.geometry_types)
        return value
