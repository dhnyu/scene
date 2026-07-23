"""Convert validated M1.3 road frames into Road Dataset APIs."""

from __future__ import annotations

from dataclasses import dataclass

from scene.roads.dataset import (
    CanonicalRoadInput,
    RoadAttributeFrame,
    RoadGeometryFrame,
    RoadLinkDataset,
    RoadNodeDataset,
)
from scene.roads.validator import RoadValidationResult, RoadValidator


_SOURCE_COLUMNS = ["source_name", "source_path", "source_file_sha256"]
_LINK_GEOMETRY_COLUMNS = [
    *_SOURCE_COLUMNS,
    "source_link_id",
    "source_fid",
    "geometry_wkb",
]
_LINK_ATTRIBUTE_COLUMNS = [
    *_SOURCE_COLUMNS,
    "source_link_id",
    "from_source_node_id",
    "to_source_node_id",
    "lanes",
    "road_rank",
    "road_type",
    "source_road_number",
    "source_road_name",
    "source_length_m",
]
_NODE_GEOMETRY_COLUMNS = [
    *_SOURCE_COLUMNS,
    "source_node_id",
    "source_fid",
    "geometry_wkb",
]
_NODE_ATTRIBUTE_COLUMNS = [
    *_SOURCE_COLUMNS,
    "source_node_id",
    "node_type",
    "node_name",
    "turn_restriction",
]


@dataclass(frozen=True, slots=True)
class RoadAdapterResult:
    """Two road datasets paired with one non-throwing validation outcome."""

    links: RoadLinkDataset
    nodes: RoadNodeDataset
    validation: RoadValidationResult


class RoadAdapter:
    """Construct unjoined road APIs without topology or new identifiers."""

    def __init__(self, validator: RoadValidator) -> None:
        self._validator = validator

    def adapt(self, canonical_input: CanonicalRoadInput) -> RoadAdapterResult:
        validation = self._validator.validate(canonical_input)
        links = RoadLinkDataset(
            geometry=RoadGeometryFrame(
                dataframe=canonical_input.link_table.select(
                    _LINK_GEOMETRY_COLUMNS
                ),
                crs=canonical_input.link_crs,
                geometry_type=canonical_input.link_geometry_type,
                bbox=validation.link_geometry.bbox,
                source_metadata=canonical_input.link_source,
                provenance_metadata=canonical_input.link_provenance,
            ),
            attributes=RoadAttributeFrame(
                dataframe=canonical_input.link_table.select(
                    _LINK_ATTRIBUTE_COLUMNS
                ),
                source_metadata=canonical_input.link_source,
                provenance_metadata=canonical_input.link_provenance,
            ),
        )
        nodes = RoadNodeDataset(
            geometry=RoadGeometryFrame(
                dataframe=canonical_input.node_table.select(
                    _NODE_GEOMETRY_COLUMNS
                ),
                crs=canonical_input.node_crs,
                geometry_type=canonical_input.node_geometry_type,
                bbox=validation.node_geometry.bbox,
                source_metadata=canonical_input.node_source,
                provenance_metadata=canonical_input.node_provenance,
            ),
            attributes=RoadAttributeFrame(
                dataframe=canonical_input.node_table.select(
                    _NODE_ATTRIBUTE_COLUMNS
                ),
                source_metadata=canonical_input.node_source,
                provenance_metadata=canonical_input.node_provenance,
            ),
        )
        return RoadAdapterResult(
            links=links,
            nodes=nodes,
            validation=validation,
        )
