"""Typed, unjoined POIDataset API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa


BoundingBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class POISourceMetadata:
    """Immutable source identity for one canonical POI frame."""

    source_name: str
    source_path: str
    source_file_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class POIProvenance:
    """M1.3 schema and artifact lineage for one POI frame."""

    canonical_run_id: str
    canonical_manifest_path: str
    canonical_schema_name: str
    canonical_schema_version: str
    canonical_schema_path: str
    canonical_schema_sha256: str
    frame_name: str
    canonical_frame_path: str
    canonical_frame_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class POIJoinKeyMetadata:
    """Source join-key names and non-mutating compatibility diagnostics."""

    canonical_column: str
    geometry_source_column: str
    attribute_source_column: str
    geometry_unique_key_count: int
    attribute_unique_key_count: int
    geometry_null_key_count: int
    attribute_null_key_count: int
    geometry_duplicate_key_count: int
    attribute_duplicate_key_count: int
    geometry_duplicate_row_count: int
    attribute_duplicate_row_count: int
    geometry_only_key_count: int
    attribute_only_key_count: int
    cardinality: str
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class POIGeometryFrame:
    """Canonical source POI points, not a model geometry modality."""

    dataframe: pa.Table
    crs: str
    geometry_type: str
    bbox: BoundingBox | None
    source_metadata: POISourceMetadata
    provenance_metadata: POIProvenance

    @property
    def feature_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class POIAttributeFrame:
    """Canonical POI categories and their derived reversible path."""

    dataframe: pa.Table
    source_metadata: POISourceMetadata
    provenance_metadata: POIProvenance

    @property
    def row_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class POIDataset:
    """Research-facing API for unjoined source POI modalities."""

    geometry: POIGeometryFrame
    attributes: POIAttributeFrame
    source_join_key_metadata: POIJoinKeyMetadata

    @property
    def geometry_dataframe(self) -> pa.Table:
        return self.geometry.dataframe

    @property
    def attribute_dataframe(self) -> pa.Table:
        return self.attributes.dataframe

    @property
    def crs(self) -> str:
        return self.geometry.crs

    @property
    def feature_count(self) -> int:
        return self.geometry.feature_count

    @property
    def attribute_row_count(self) -> int:
        return self.attributes.row_count

    @property
    def bounding_box(self) -> BoundingBox | None:
        return self.geometry.bbox

    @property
    def source_metadata(self) -> dict[str, POISourceMetadata]:
        return {
            "geometry": self.geometry.source_metadata,
            "attributes": self.attributes.source_metadata,
        }

    @property
    def provenance_metadata(self) -> dict[str, POIProvenance]:
        return {
            "geometry": self.geometry.provenance_metadata,
            "attributes": self.attributes.provenance_metadata,
        }

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "attribute_row_count": self.attribute_row_count,
            "bbox": list(self.bounding_box) if self.bounding_box else None,
            "crs": self.crs,
            "feature_count": self.feature_count,
            "geometry_type": self.geometry.geometry_type,
            "provenance_metadata": {
                key: value.to_dict()
                for key, value in self.provenance_metadata.items()
            },
            "source_join_key_metadata": (
                self.source_join_key_metadata.to_dict()
            ),
            "source_metadata": {
                key: value.to_dict()
                for key, value in self.source_metadata.items()
            },
        }


@dataclass(frozen=True, slots=True)
class CanonicalPOIInput:
    """Reader output before POIDataset validation and adaptation."""

    geometry_table: pa.Table
    attribute_table: pa.Table
    geometry_source: POISourceMetadata
    attribute_source: POISourceMetadata
    geometry_provenance: POIProvenance
    attribute_provenance: POIProvenance
    geometry_crs: str
    geometry_type: str
    geometry_expected_rows: int
    attribute_expected_rows: int
    manifest_path: Path
