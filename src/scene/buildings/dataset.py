"""Typed, unjoined BuildingDataset API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa


BoundingBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class BuildingSourceMetadata:
    """Immutable source identity carried by one canonical building frame."""

    source_name: str
    source_path: str
    source_file_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BuildingProvenance:
    """M1.3 schema and artifact lineage for one building frame."""

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
class BuildingGeometryFrame:
    """Canonical source building geometry before scene clipping or stable IDs."""

    dataframe: pa.Table
    crs: str
    geometry_type: str
    bbox: BoundingBox | None
    source_metadata: BuildingSourceMetadata
    provenance_metadata: BuildingProvenance

    @property
    def feature_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class BuildingAttributeFrame:
    """Canonical building attributes kept separate from geometry."""

    dataframe: pa.Table
    source_metadata: BuildingSourceMetadata
    provenance_metadata: BuildingProvenance

    @property
    def row_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class BuildingDataset:
    """Research-facing API for unjoined source building modalities."""

    geometry: BuildingGeometryFrame
    attributes: BuildingAttributeFrame

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
    def source_metadata(self) -> dict[str, BuildingSourceMetadata]:
        return {
            "geometry": self.geometry.source_metadata,
            "attributes": self.attributes.source_metadata,
        }

    @property
    def provenance_metadata(self) -> dict[str, BuildingProvenance]:
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
            "source_metadata": {
                key: value.to_dict()
                for key, value in self.source_metadata.items()
            },
        }


@dataclass(frozen=True, slots=True)
class CanonicalBuildingInput:
    """Reader output before BuildingDataset validation and adaptation."""

    geometry_table: pa.Table
    attribute_table: pa.Table
    geometry_source: BuildingSourceMetadata
    attribute_source: BuildingSourceMetadata
    geometry_provenance: BuildingProvenance
    attribute_provenance: BuildingProvenance
    geometry_crs: str
    geometry_type: str
    geometry_expected_rows: int
    attribute_expected_rows: int
    manifest_path: Path
