"""Full-frame POI validation and non-mutating join-key diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely

from scene.pois.category import (
    CATEGORY_COLUMNS,
    CATEGORY_NORMALIZATION,
    build_category_path,
)
from scene.pois.dataset import (
    BoundingBox,
    CanonicalPOIInput,
    POIJoinKeyMetadata,
)
from scene.schema.models import CanonicalFrameSchema, CanonicalSchema
from scene.schema.typing import arrow_type


@dataclass(frozen=True, slots=True)
class POIValidationIssue:
    """One POI Adapter contract violation."""

    code: str
    message: str
    frame: str | None = None
    column: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class POIValidationResult:
    """Complete M1.4.3 validation and diagnostics."""

    geometry_row_count: int
    attribute_row_count: int
    expected_geometry_row_count: int
    expected_attribute_row_count: int
    geometry_null_count: int
    geometry_parse_failure_count: int
    unexpected_geometry_type_count: int
    empty_geometry_count: int
    bbox: BoundingBox | None
    crs: str
    geometry_type: str
    category_null_counts: tuple[int, ...]
    category_empty_counts: tuple[int, ...]
    category_normalization: str
    join_key: POIJoinKeyMetadata
    row_counts_valid: bool
    geometry_valid: bool
    attributes_valid: bool
    crs_valid: bool
    source_metadata_valid: bool
    canonical_schema_valid: bool
    category_hierarchy_valid: bool
    category_path_valid: bool
    source_labels_preserved: bool
    modalities_unjoined: bool
    stable_id_created: bool
    geometry_modality_created: bool
    issues: tuple[POIValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return (
            self.row_counts_valid
            and self.geometry_valid
            and self.attributes_valid
            and self.crs_valid
            and self.source_metadata_valid
            and self.canonical_schema_valid
            and self.category_hierarchy_valid
            and self.category_path_valid
            and self.source_labels_preserved
            and self.join_key.valid
            and self.modalities_unjoined
            and not self.stable_id_created
            and not self.geometry_modality_created
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = list(self.bbox) if self.bbox else None
        value["join_key"] = self.join_key.to_dict()
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
    issues: list[POIValidationIssue],
) -> bool:
    valid = True
    expected_names = [column.column for column in spec.columns]
    if table.column_names != expected_names:
        issues.append(
            POIValidationIssue(
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
                POIValidationIssue(
                    code="canonical_dtype_mismatch",
                    message=f"expected {expected_type}, got {field.type}",
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        if field.nullable != column.nullable:
            issues.append(
                POIValidationIssue(
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
        metadata = _decode_metadata(field.metadata)
        if metadata.get("source_column") != column.source_column:
            issues.append(
                POIValidationIssue(
                    code="canonical_mapping_mismatch",
                    message=(
                        f"expected source_column={column.source_column}, "
                        f"got {metadata.get('source_column')}"
                    ),
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        if not column.nullable and table[column.column].null_count:
            issues.append(
                POIValidationIssue(
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
    metadata = _decode_metadata(table.schema.metadata)
    expected_metadata = {
        "scene:canonical_schema_version": schema_version,
        "scene:frame_name": spec.frame_name,
        "scene:source_name": spec.source_name,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            issues.append(
                POIValidationIssue(
                    code="canonical_schema_metadata_mismatch",
                    message=(
                        f"{key} expected {expected}, got {metadata.get(key)}"
                    ),
                    frame=spec.frame_name,
                )
            )
            valid = False
    return valid


def _inspect_geometry(
    table: pa.Table,
) -> tuple[int, int, int, int, BoundingBox | None]:
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
                shapely.get_type_id(present)
                != int(shapely.GeometryType.POINT)
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
    return (
        geometry.null_count,
        parse_failures,
        unexpected_types,
        empty_count,
        bbox,
    )


def _duplicate_counts(values: pa.ChunkedArray) -> tuple[int, int, int]:
    unique = pc.drop_null(pc.unique(values))
    counts = pc.value_counts(pc.drop_null(values)).field("counts")
    duplicate_keys = int(
        pc.sum(pc.cast(pc.greater(counts, 1), pa.int64())).as_py() or 0
    )
    return len(unique), duplicate_keys, len(values) - values.null_count - len(unique)


def _join_key_diagnostics(
    geometry: pa.ChunkedArray,
    attributes: pa.ChunkedArray,
) -> POIJoinKeyMetadata:
    geometry_unique, geometry_duplicate_keys, geometry_duplicate_rows = (
        _duplicate_counts(geometry)
    )
    attribute_unique, attribute_duplicate_keys, attribute_duplicate_rows = (
        _duplicate_counts(attributes)
    )
    geometry_values = pc.drop_null(pc.unique(geometry))
    attribute_values = pc.drop_null(pc.unique(attributes))
    geometry_only = int(
        pc.sum(
            pc.cast(
                pc.invert(pc.is_in(geometry_values, value_set=attribute_values)),
                pa.int64(),
            )
        ).as_py()
        or 0
    )
    attribute_only = int(
        pc.sum(
            pc.cast(
                pc.invert(pc.is_in(attribute_values, value_set=geometry_values)),
                pa.int64(),
            )
        ).as_py()
        or 0
    )
    if geometry_duplicate_keys and attribute_duplicate_keys:
        cardinality = "many_to_many"
    elif geometry_duplicate_keys:
        cardinality = "many_to_one"
    elif attribute_duplicate_keys:
        cardinality = "one_to_many"
    else:
        cardinality = "one_to_one"
    valid = (
        geometry.null_count == 0
        and attributes.null_count == 0
        and geometry_duplicate_keys == 0
        and attribute_duplicate_keys == 0
        and geometry_only == 0
        and attribute_only == 0
    )
    return POIJoinKeyMetadata(
        canonical_column="source_poi_id",
        geometry_source_column="NF_ID",
        attribute_source_column="NF_ID",
        geometry_unique_key_count=geometry_unique,
        attribute_unique_key_count=attribute_unique,
        geometry_null_key_count=geometry.null_count,
        attribute_null_key_count=attributes.null_count,
        geometry_duplicate_key_count=geometry_duplicate_keys,
        attribute_duplicate_key_count=attribute_duplicate_keys,
        geometry_duplicate_row_count=geometry_duplicate_rows,
        attribute_duplicate_row_count=attribute_duplicate_rows,
        geometry_only_key_count=geometry_only,
        attribute_only_key_count=attribute_only,
        cardinality=cardinality,
        valid=valid,
    )


def _source_metadata_matches(
    canonical_input: CanonicalPOIInput,
    issues: list[POIValidationIssue],
) -> bool:
    valid = True
    pairs = (
        (
            "poi_geometry",
            canonical_input.geometry_table,
            canonical_input.geometry_source,
        ),
        (
            "poi_attribute",
            canonical_input.attribute_table,
            canonical_input.attribute_source,
        ),
    )
    for frame_name, table, source in pairs:
        metadata = _decode_metadata(table.schema.metadata)
        expected = {
            "source_name": source.source_name,
            "source_path": source.source_path,
            "source_file_sha256": source.source_file_sha256,
        }
        for column, value in expected.items():
            if table[column].unique().to_pylist() != [value]:
                issues.append(
                    POIValidationIssue(
                        code="source_metadata_not_constant",
                        message=f"{column} values do not match source metadata",
                        frame=frame_name,
                        column=column,
                    )
                )
                valid = False
        if (
            metadata.get("scene:source_name") != source.source_name
            or metadata.get("scene:source_file_sha256")
            != source.source_file_sha256
        ):
            issues.append(
                POIValidationIssue(
                    code="source_schema_metadata_mismatch",
                    message="source identity differs from Arrow schema metadata",
                    frame=frame_name,
                )
            )
            valid = False
    return valid


class POIValidator:
    """Validate canonical POI frames without joining or mutating rows."""

    def __init__(self, schema: CanonicalSchema) -> None:
        self._schema = schema

    def validate(
        self,
        canonical_input: CanonicalPOIInput,
    ) -> POIValidationResult:
        issues: list[POIValidationIssue] = []
        geometry_spec = self._schema.frame_for("seoul_poi_geometry")
        attribute_spec = self._schema.frame_for("seoul_poi_attributes")
        geometry_schema_valid = _validate_schema(
            canonical_input.geometry_table,
            geometry_spec,
            self._schema.schema_version,
            issues,
        )
        attribute_schema_valid = _validate_schema(
            canonical_input.attribute_table,
            attribute_spec,
            self._schema.schema_version,
            issues,
        )
        row_counts_valid = (
            canonical_input.geometry_table.num_rows
            == canonical_input.geometry_expected_rows
            and canonical_input.attribute_table.num_rows
            == canonical_input.attribute_expected_rows
        )
        if not row_counts_valid:
            issues.append(
                POIValidationIssue(
                    code="row_count_mismatch",
                    message="canonical POI row count differs from M1.3 manifest",
                )
            )

        (
            geometry_null_count,
            geometry_parse_failure_count,
            unexpected_geometry_type_count,
            empty_geometry_count,
            bbox,
        ) = _inspect_geometry(canonical_input.geometry_table)
        geometry_valid = (
            geometry_null_count == 0
            and geometry_parse_failure_count == 0
            and unexpected_geometry_type_count == 0
            and empty_geometry_count == 0
            and bbox is not None
        )
        for code, count in (
            ("geometry_null", geometry_null_count),
            ("geometry_parse_failure", geometry_parse_failure_count),
            ("unexpected_geometry_type", unexpected_geometry_type_count),
            ("empty_geometry", empty_geometry_count),
        ):
            if count:
                issues.append(
                    POIValidationIssue(
                        code=code,
                        message=f"{count} geometry rows violate {code}",
                        frame=geometry_spec.frame_name,
                        column="geometry_wkb",
                    )
                )
        if bbox is None:
            issues.append(
                POIValidationIssue(
                    code="bbox_unavailable",
                    message="no finite POI geometry bbox could be calculated",
                    frame=geometry_spec.frame_name,
                )
            )

        crs_valid = (
            canonical_input.geometry_crs == self._schema.canonical_crs
            and canonical_input.geometry_type == "Point"
        )
        if not crs_valid:
            issues.append(
                POIValidationIssue(
                    code="spatial_metadata_mismatch",
                    message=(
                        f"expected EPSG:5186 Point, got "
                        f"{canonical_input.geometry_crs} "
                        f"{canonical_input.geometry_type}"
                    ),
                    frame=geometry_spec.frame_name,
                )
            )

        join_key = _join_key_diagnostics(
            canonical_input.geometry_table["source_poi_id"],
            canonical_input.attribute_table["source_poi_id"],
        )
        if not join_key.valid:
            issues.append(
                POIValidationIssue(
                    code="join_key_contract_violation",
                    message=(
                        "source_poi_id does not satisfy one-to-one 100% match"
                    ),
                    column="source_poi_id",
                )
            )

        category_fields_valid = all(
            column in canonical_input.attribute_table.column_names
            for column in CATEGORY_COLUMNS
        )
        category_null_counts = tuple(
            canonical_input.attribute_table[column].null_count
            if column in canonical_input.attribute_table.column_names
            else canonical_input.attribute_table.num_rows
            for column in CATEGORY_COLUMNS
        )
        category_empty_counts = tuple(
            int(
                pc.sum(
                    pc.cast(
                        pc.equal(canonical_input.attribute_table[column], ""),
                        pa.int64(),
                    )
                ).as_py()
                or 0
            )
            if column in canonical_input.attribute_table.column_names
            else canonical_input.attribute_table.num_rows
            for column in CATEGORY_COLUMNS
        )
        category_path_valid = False
        if category_fields_valid:
            path = build_category_path(canonical_input.attribute_table)
            category_path_valid = (
                len(path) == canonical_input.attribute_table.num_rows
                and path.null_count == 0
            )
        else:
            issues.append(
                POIValidationIssue(
                    code="category_hierarchy_missing",
                    message="one or more six-stage POI category fields are absent",
                    frame=attribute_spec.frame_name,
                )
            )
        if not category_path_valid:
            issues.append(
                POIValidationIssue(
                    code="category_path_invalid",
                    message="six-stage category path could not be generated",
                    frame=attribute_spec.frame_name,
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
            "observed_geometry",
            "geometry_embedding",
            "poi_polygon",
        }
        present_forbidden = forbidden.intersection(
            canonical_input.geometry_table.column_names
            + canonical_input.attribute_table.column_names
        )
        modalities_unjoined = not present_forbidden
        if present_forbidden:
            issues.append(
                POIValidationIssue(
                    code="forbidden_derived_field",
                    message=(
                        "M1.4.3 contains forbidden fields: "
                        f"{sorted(present_forbidden)}"
                    ),
                )
            )

        return POIValidationResult(
            geometry_row_count=canonical_input.geometry_table.num_rows,
            attribute_row_count=canonical_input.attribute_table.num_rows,
            expected_geometry_row_count=canonical_input.geometry_expected_rows,
            expected_attribute_row_count=canonical_input.attribute_expected_rows,
            geometry_null_count=geometry_null_count,
            geometry_parse_failure_count=geometry_parse_failure_count,
            unexpected_geometry_type_count=unexpected_geometry_type_count,
            empty_geometry_count=empty_geometry_count,
            bbox=bbox,
            crs=canonical_input.geometry_crs,
            geometry_type=canonical_input.geometry_type,
            category_null_counts=category_null_counts,
            category_empty_counts=category_empty_counts,
            category_normalization=CATEGORY_NORMALIZATION,
            join_key=join_key,
            row_counts_valid=row_counts_valid,
            geometry_valid=geometry_valid,
            attributes_valid=attribute_schema_valid,
            crs_valid=crs_valid,
            source_metadata_valid=source_metadata_valid,
            canonical_schema_valid=(
                geometry_schema_valid and attribute_schema_valid
            ),
            category_hierarchy_valid=category_fields_valid,
            category_path_valid=category_path_valid,
            source_labels_preserved=True,
            modalities_unjoined=modalities_unjoined,
            stable_id_created=False,
            geometry_modality_created=False,
            issues=tuple(issues),
        )
