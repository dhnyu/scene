"""Full-frame road validation without joins or topology construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pyarrow as pa
import shapely

from scene.roads.dataset import BoundingBox, CanonicalRoadInput
from scene.schema.models import CanonicalFrameSchema, CanonicalSchema
from scene.schema.typing import arrow_type


@dataclass(frozen=True, slots=True)
class RoadValidationIssue:
    """One Road Adapter contract violation."""

    code: str
    message: str
    frame: str | None = None
    column: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GeometryValidation:
    """Full geometry-column inspection result."""

    row_count: int
    null_count: int
    parse_failure_count: int
    unexpected_type_count: int
    empty_count: int
    bbox: BoundingBox | None
    expected_type: str

    @property
    def valid(self) -> bool:
        return (
            self.null_count == 0
            and self.parse_failure_count == 0
            and self.unexpected_type_count == 0
            and self.empty_count == 0
            and self.bbox is not None
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = list(self.bbox) if self.bbox else None
        value["valid"] = self.valid
        return value


@dataclass(frozen=True, slots=True)
class RoadValidationResult:
    """Complete M1.4.2 validation outcome."""

    link_geometry: GeometryValidation
    node_geometry: GeometryValidation
    link_expected_row_count: int
    node_expected_row_count: int
    row_counts_valid: bool
    link_attributes_valid: bool
    node_attributes_valid: bool
    crs_valid: bool
    source_metadata_valid: bool
    canonical_schema_valid: bool
    projections_unjoined: bool
    topology_created: bool
    stable_id_created: bool
    available_road_fields: tuple[str, ...]
    unavailable_road_concepts: tuple[str, ...]
    issues: tuple[RoadValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return (
            self.link_geometry.valid
            and self.node_geometry.valid
            and self.row_counts_valid
            and self.link_attributes_valid
            and self.node_attributes_valid
            and self.crs_valid
            and self.source_metadata_valid
            and self.canonical_schema_valid
            and self.projections_unjoined
            and not self.topology_created
            and not self.stable_id_created
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["link_geometry"] = self.link_geometry.to_dict()
        value["node_geometry"] = self.node_geometry.to_dict()
        value["issues"] = [issue.to_dict() for issue in self.issues]
        value["valid"] = self.valid
        return value


def _decode_metadata(
    metadata: dict[bytes, bytes] | None,
) -> dict[str, str]:
    return {
        key.decode("utf-8"): value.decode("utf-8")
        for key, value in (metadata or {}).items()
    }


def _validate_schema(
    table: pa.Table,
    spec: CanonicalFrameSchema,
    schema_version: str,
    issues: list[RoadValidationIssue],
) -> bool:
    valid = True
    expected_names = [column.column for column in spec.columns]
    if table.column_names != expected_names:
        issues.append(
            RoadValidationIssue(
                code="canonical_columns_mismatch",
                message=f"expected {expected_names}, got {table.column_names}",
                frame=spec.frame_name,
            )
        )
        valid = False
    for column in spec.columns:
        index = table.schema.get_field_index(column.column)
        if index < 0:
            continue
        field = table.schema.field(index)
        expected_type = arrow_type(column.dtype)
        if field.type != expected_type:
            issues.append(
                RoadValidationIssue(
                    code="canonical_dtype_mismatch",
                    message=f"expected {expected_type}, got {field.type}",
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        if field.nullable != column.nullable:
            issues.append(
                RoadValidationIssue(
                    code="canonical_nullable_mismatch",
                    message=(
                        f"expected nullable={column.nullable}, "
                        f"got {field.nullable}"
                    ),
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        field_metadata = _decode_metadata(field.metadata)
        if field_metadata.get("source_column") != column.source_column:
            issues.append(
                RoadValidationIssue(
                    code="canonical_mapping_mismatch",
                    message=(
                        f"expected source_column={column.source_column}, "
                        f"got {field_metadata.get('source_column')}"
                    ),
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        if not column.nullable and table[column.column].null_count:
            issues.append(
                RoadValidationIssue(
                    code="attribute_nullability_violation",
                    message=(
                        f"non-nullable column contains "
                        f"{table[column.column].null_count} null values"
                    ),
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
    schema_metadata = _decode_metadata(table.schema.metadata)
    expected_metadata = {
        "scene:canonical_schema_version": schema_version,
        "scene:frame_name": spec.frame_name,
        "scene:source_name": spec.source_name,
    }
    for key, expected in expected_metadata.items():
        if schema_metadata.get(key) != expected:
            issues.append(
                RoadValidationIssue(
                    code="canonical_schema_metadata_mismatch",
                    message=(
                        f"{key} expected {expected}, "
                        f"got {schema_metadata.get(key)}"
                    ),
                    frame=spec.frame_name,
                )
            )
            valid = False
    return valid


def _inspect_geometry(
    table: pa.Table,
    expected_type: shapely.GeometryType,
) -> GeometryValidation:
    geometry = table["geometry_wkb"]
    parse_failures = 0
    unexpected_types = 0
    empty_count = 0
    min_x, min_y = math.inf, math.inf
    max_x, max_y = -math.inf, -math.inf
    for chunk in geometry.chunks:
        values = chunk.to_numpy(zero_copy_only=False)
        geometries = shapely.from_wkb(values, on_invalid="ignore")
        missing = shapely.is_missing(geometries)
        parse_failures += max(
            int(np.count_nonzero(missing)) - chunk.null_count, 0
        )
        present = geometries[~missing]
        if len(present) == 0:
            continue
        unexpected_types += int(
            np.count_nonzero(
                shapely.get_type_id(present) != int(expected_type)
            )
        )
        empty = shapely.is_empty(present)
        empty_count += int(np.count_nonzero(empty))
        usable = present[~empty]
        if len(usable) == 0:
            continue
        bounds = shapely.bounds(usable)
        finite = np.all(np.isfinite(bounds), axis=1)
        bounds = bounds[finite]
        if len(bounds) == 0:
            continue
        min_x = min(min_x, float(np.min(bounds[:, 0])))
        min_y = min(min_y, float(np.min(bounds[:, 1])))
        max_x = max(max_x, float(np.max(bounds[:, 2])))
        max_y = max(max_y, float(np.max(bounds[:, 3])))
    bbox = (
        (min_x, min_y, max_x, max_y)
        if all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y))
        else None
    )
    return GeometryValidation(
        row_count=table.num_rows,
        null_count=geometry.null_count,
        parse_failure_count=parse_failures,
        unexpected_type_count=unexpected_types,
        empty_count=empty_count,
        bbox=bbox,
        expected_type={
            shapely.GeometryType.LINESTRING: "LineString",
            shapely.GeometryType.POINT: "Point",
        }[expected_type],
    )


def _source_metadata_matches(
    canonical_input: CanonicalRoadInput,
    issues: list[RoadValidationIssue],
) -> bool:
    valid = True
    pairs = (
        ("road_link", canonical_input.link_table, canonical_input.link_source),
        ("road_node", canonical_input.node_table, canonical_input.node_source),
    )
    for frame_name, table, source in pairs:
        schema_metadata = _decode_metadata(table.schema.metadata)
        expected = {
            "source_name": source.source_name,
            "source_path": source.source_path,
            "source_file_sha256": source.source_file_sha256,
        }
        for column, value in expected.items():
            if table[column].unique().to_pylist() != [value]:
                issues.append(
                    RoadValidationIssue(
                        code="source_metadata_not_constant",
                        message=f"{column} values do not match source metadata",
                        frame=frame_name,
                        column=column,
                    )
                )
                valid = False
        if (
            schema_metadata.get("scene:source_name") != source.source_name
            or schema_metadata.get("scene:source_file_sha256")
            != source.source_file_sha256
        ):
            issues.append(
                RoadValidationIssue(
                    code="source_schema_metadata_mismatch",
                    message="source identity differs from Arrow schema metadata",
                    frame=frame_name,
                )
            )
            valid = False
    return valid


class RoadValidator:
    """Validate canonical road frames without connecting link and node rows."""

    def __init__(self, schema: CanonicalSchema) -> None:
        self._schema = schema

    def validate(
        self,
        canonical_input: CanonicalRoadInput,
    ) -> RoadValidationResult:
        """Return all detected contract violations without stopping early."""

        issues: list[RoadValidationIssue] = []
        link_spec = self._schema.frame_for("seoul_roads_links")
        node_spec = self._schema.frame_for("seoul_roads_nodes")
        link_schema_valid = _validate_schema(
            canonical_input.link_table,
            link_spec,
            self._schema.schema_version,
            issues,
        )
        node_schema_valid = _validate_schema(
            canonical_input.node_table,
            node_spec,
            self._schema.schema_version,
            issues,
        )
        row_counts_valid = (
            canonical_input.link_table.num_rows
            == canonical_input.link_expected_rows
            and canonical_input.node_table.num_rows
            == canonical_input.node_expected_rows
        )
        if not row_counts_valid:
            issues.append(
                RoadValidationIssue(
                    code="row_count_mismatch",
                    message="canonical road row count differs from M1.3 manifest",
                )
            )

        link_geometry = _inspect_geometry(
            canonical_input.link_table, shapely.GeometryType.LINESTRING
        )
        node_geometry = _inspect_geometry(
            canonical_input.node_table, shapely.GeometryType.POINT
        )
        for frame, result in (
            (link_spec.frame_name, link_geometry),
            (node_spec.frame_name, node_geometry),
        ):
            for code, count in (
                ("geometry_null", result.null_count),
                ("geometry_parse_failure", result.parse_failure_count),
                ("unexpected_geometry_type", result.unexpected_type_count),
                ("empty_geometry", result.empty_count),
            ):
                if count:
                    issues.append(
                        RoadValidationIssue(
                            code=code,
                            message=f"{count} geometry rows violate {code}",
                            frame=frame,
                            column="geometry_wkb",
                        )
                    )
            if result.bbox is None:
                issues.append(
                    RoadValidationIssue(
                        code="bbox_unavailable",
                        message="no finite road geometry bbox could be calculated",
                        frame=frame,
                    )
                )

        crs_valid = (
            canonical_input.link_crs == self._schema.canonical_crs
            and canonical_input.node_crs == self._schema.canonical_crs
            and canonical_input.link_geometry_type == "LineString"
            and canonical_input.node_geometry_type == "Point"
        )
        if not crs_valid:
            issues.append(
                RoadValidationIssue(
                    code="spatial_metadata_mismatch",
                    message=(
                        "expected EPSG:5186 LineString links and Point nodes, "
                        f"got {canonical_input.link_crs} "
                        f"{canonical_input.link_geometry_type} and "
                        f"{canonical_input.node_crs} "
                        f"{canonical_input.node_geometry_type}"
                    ),
                )
            )

        source_metadata_valid = _source_metadata_matches(
            canonical_input, issues
        )
        forbidden = {
            "source_object_id",
            "stable_id",
            "scene_id",
            "relation_id",
            "parent_way_id",
            "bridge",
            "tunnel",
            "direction",
        }
        present_forbidden = forbidden.intersection(
            canonical_input.link_table.column_names
            + canonical_input.node_table.column_names
        )
        projections_unjoined = not present_forbidden
        if present_forbidden:
            issues.append(
                RoadValidationIssue(
                    code="forbidden_derived_field",
                    message=(
                        "M1.4.2 contains unsupported or derived fields: "
                        f"{sorted(present_forbidden)}"
                    ),
                )
            )

        return RoadValidationResult(
            link_geometry=link_geometry,
            node_geometry=node_geometry,
            link_expected_row_count=canonical_input.link_expected_rows,
            node_expected_row_count=canonical_input.node_expected_rows,
            row_counts_valid=row_counts_valid,
            link_attributes_valid=link_schema_valid,
            node_attributes_valid=node_schema_valid,
            crs_valid=crs_valid,
            source_metadata_valid=source_metadata_valid,
            canonical_schema_valid=link_schema_valid and node_schema_valid,
            projections_unjoined=projections_unjoined,
            topology_created=False,
            stable_id_created=False,
            available_road_fields=(
                "road_type",
                "road_rank",
                "source_road_name",
            ),
            unavailable_road_concepts=("bridge", "tunnel", "direction"),
            issues=tuple(issues),
        )
