"""Typed input and output provenance for stable IDs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """Contracted mapping from one canonical frame to one ID domain."""

    entity_type: str
    object_type: str
    id_name: str
    source_name: str
    source_native_id_field: str


ENTITY_SPECS = (
    EntitySpec(
        entity_type="building",
        object_type="building",
        id_name="building_id",
        source_name="seoul_buildings_geometry",
        source_native_id_field="source_building_id",
    ),
    EntitySpec(
        entity_type="road_link",
        object_type="road",
        id_name="road_link_id",
        source_name="seoul_roads_links",
        source_native_id_field="source_link_id",
    ),
    EntitySpec(
        entity_type="road_node",
        object_type="road",
        id_name="road_node_id",
        source_name="seoul_roads_nodes",
        source_native_id_field="source_node_id",
    ),
    EntitySpec(
        entity_type="poi",
        object_type="poi",
        id_name="poi_id",
        source_name="seoul_poi_geometry",
        source_native_id_field="source_poi_id",
    ),
)


@dataclass(frozen=True, slots=True)
class CanonicalIdFrame:
    """Integrity-checked canonical frame reference."""

    spec: EntitySpec
    path: Path
    sha256: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


@dataclass(frozen=True, slots=True)
class StableIdInput:
    """Canonical manifest and four source frame references."""

    canonical_manifest_path: Path
    canonical_manifest_sha256: str
    canonical_run_id: str
    schema_name: str
    schema_version: str
    schema_sha256: str
    frames: tuple[CanonicalIdFrame, ...]

    @property
    def expected_row_count(self) -> int:
        return sum(frame.row_count for frame in self.frames)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_manifest_path": str(self.canonical_manifest_path),
            "canonical_manifest_sha256": self.canonical_manifest_sha256,
            "canonical_run_id": self.canonical_run_id,
            "expected_row_count": self.expected_row_count,
            "frames": [frame.to_dict() for frame in self.frames],
            "schema_name": self.schema_name,
            "schema_sha256": self.schema_sha256,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class StableIdDataset:
    """Materialized ID registry and matching provenance rows."""

    ids: pa.Table
    provenance: pa.Table
    generation_digest: str
    source: StableIdInput

    @property
    def row_count(self) -> int:
        return self.ids.num_rows

    @property
    def counts(self) -> dict[str, int]:
        values = self.ids["entity_type"].combine_chunks().to_pylist()
        return {
            spec.entity_type: values.count(spec.entity_type)
            for spec in ENTITY_SPECS
        }
