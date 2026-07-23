"""Typed, unjoined Road Link and Road Node dataset APIs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa


BoundingBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class RoadSourceMetadata:
    """Immutable source identity carried by one canonical road frame."""

    source_name: str
    source_path: str
    source_file_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RoadProvenance:
    """M1.3 schema and artifact lineage for one road frame."""

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
class RoadGeometryFrame:
    """Canonical source road geometry before clipping or stable IDs."""

    dataframe: pa.Table
    crs: str
    geometry_type: str
    bbox: BoundingBox | None
    source_metadata: RoadSourceMetadata
    provenance_metadata: RoadProvenance

    @property
    def feature_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class RoadAttributeFrame:
    """Canonical road attributes kept separate from geometry."""

    dataframe: pa.Table
    source_metadata: RoadSourceMetadata
    provenance_metadata: RoadProvenance

    @property
    def row_count(self) -> int:
        return self.dataframe.num_rows


@dataclass(frozen=True, slots=True)
class RoadLinkDataset:
    """Research-facing API for unjoined source road-link modalities."""

    geometry: RoadGeometryFrame
    attributes: RoadAttributeFrame

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
    def source_metadata(self) -> RoadSourceMetadata:
        return self.geometry.source_metadata

    @property
    def provenance_metadata(self) -> RoadProvenance:
        return self.geometry.provenance_metadata

    def metadata_dict(self) -> dict[str, Any]:
        return _dataset_metadata(self)


@dataclass(frozen=True, slots=True)
class RoadNodeDataset:
    """Research-facing API for unjoined source road-node modalities."""

    geometry: RoadGeometryFrame
    attributes: RoadAttributeFrame

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
    def source_metadata(self) -> RoadSourceMetadata:
        return self.geometry.source_metadata

    @property
    def provenance_metadata(self) -> RoadProvenance:
        return self.geometry.provenance_metadata

    def metadata_dict(self) -> dict[str, Any]:
        return _dataset_metadata(self)


def _dataset_metadata(
    dataset: RoadLinkDataset | RoadNodeDataset,
) -> dict[str, Any]:
    return {
        "attribute_row_count": dataset.attribute_row_count,
        "bbox": list(dataset.bounding_box) if dataset.bounding_box else None,
        "crs": dataset.crs,
        "feature_count": dataset.feature_count,
        "geometry_type": dataset.geometry.geometry_type,
        "provenance_metadata": dataset.geometry.provenance_metadata.to_dict(),
        "source_metadata": dataset.geometry.source_metadata.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class CanonicalRoadInput:
    """Reader output before road validation and projection."""

    link_table: pa.Table
    node_table: pa.Table
    link_source: RoadSourceMetadata
    node_source: RoadSourceMetadata
    link_provenance: RoadProvenance
    node_provenance: RoadProvenance
    link_crs: str
    node_crs: str
    link_geometry_type: str
    node_geometry_type: str
    link_expected_rows: int
    node_expected_rows: int
    manifest_path: Path
