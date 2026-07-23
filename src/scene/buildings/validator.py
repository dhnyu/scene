"""Full-frame BuildingDataset validation without joining modalities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pyarrow as pa
import shapely

from scene.buildings.dataset import BoundingBox, CanonicalBuildingInput
from scene.schema.models import CanonicalFrameSchema, CanonicalSchema
from scene.schema.typing import arrow_type


@dataclass(frozen=True, slots=True)
class BuildingValidationIssue:
    """One Building Adapter contract violation."""

    code: str
    message: str
    frame: str | None = None
    column: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BuildingValidationResult:
    """Complete M1.4.1 validation outcome."""

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
    row_counts_valid: bool
    geometry_valid: bool
    attributes_valid: bool
    crs_valid: bool
    source_metadata_valid: bool
    canonical_schema_valid: bool
    modalities_unjoined: bool
    issues: tuple[BuildingValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return (
            self.row_counts_valid
            and self.geometry_valid
            and self.attributes_valid
            and self.crs_valid
            and self.source_metadata_valid
            and self.canonical_schema_valid
            and self.modalities_unjoined
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = list(self.bbox) if self.bbox is not None else None
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
    issues: list[BuildingValidationIssue],
) -> bool:
    valid = True
    expected_names = [column.column for column in spec.columns]
    if table.column_names != expected_names:
        issues.append(
            BuildingValidationIssue(
                code="canonical_columns_mismatch",
                message=(
                    f"expected {expected_names}, got {table.column_names}"
                ),
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
                BuildingValidationIssue(
                    code="canonical_dtype_mismatch",
                    message=f"expected {expected_type}, got {field.type}",
                    frame=spec.frame_name,
                    column=column.column,
                )
            )
            valid = False
        if field.nullable != column.nullable:
            issues.append(
                BuildingValidationIssue(
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
                BuildingValidationIssue(
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
                BuildingValidationIssue(
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
                BuildingValidationIssue(
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
) -> tuple[int, int, int, int, BoundingBox | None]:
    geometry = table["geometry_wkb"]
    null_count = geometry.null_count
    parse_failures = 0
    unexpected_types = 0
    empty_count = 0
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    for chunk in geometry.chunks:
        values = chunk.to_numpy(zero_copy_only=False)
        geometries = shapely.from_wkb(values, on_invalid="ignore")
        missing = shapely.is_missing(geometries)
        parse_failures += max(int(np.count_nonzero(missing)) - chunk.null_count, 0)
        nonmissing = ~missing
        if not np.any(nonmissing):
            continue
        present = geometries[nonmissing]
        geometry_types = shapely.get_type_id(present)
        unexpected_types += int(
            np.count_nonzero(
                geometry_types != int(shapely.GeometryType.MULTIPOLYGON)
            )
        )
        empty = shapely.is_empty(present)
        empty_count += int(np.count_nonzero(empty))
        usable = present[~empty]
        if len(usable) == 0:
            continue
        bounds = shapely.bounds(usable)
        if not np.all(np.isfinite(bounds)):
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
    return null_count, parse_failures, unexpected_types, empty_count, bbox


def _source_metadata_matches(
    canonical_input: CanonicalBuildingInput,
    issues: list[BuildingValidationIssue],
) -> bool:
    valid = True
    pairs = (
        (
            "building_geometry",
            canonical_input.geometry_table,
            canonical_input.geometry_source,
        ),
        (
            "building_attribute",
            canonical_input.attribute_table,
            canonical_input.attribute_source,
        ),
    )
    for frame_name, table, source in pairs:
        schema_metadata = _decode_metadata(table.schema.metadata)
        expected = {
            "source_name": source.source_name,
            "source_path": source.source_path,
            "source_file_sha256": source.source_file_sha256,
        }
        for column, value in expected.items():
            values = table[column].unique().to_pylist()
            if values != [value]:
                issues.append(
                    BuildingValidationIssue(
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
                BuildingValidationIssue(
                    code="source_schema_metadata_mismatch",
                    message="source identity differs from Arrow schema metadata",
                    frame=frame_name,
                )
            )
            valid = False
    return valid


class BuildingValidator:
    """Validate full canonical building frames against current contracts."""

    def __init__(self, schema: CanonicalSchema) -> None:
        self._schema = schema

    def validate(
        self,
        canonical_input: CanonicalBuildingInput,
    ) -> BuildingValidationResult:
        """Return all detected violations without joining building frames."""

        issues: list[BuildingValidationIssue] = []
        geometry_spec = self._schema.frame_for("seoul_buildings_geometry")
        attribute_spec = self._schema.frame_for(
            "seoul_buildings_attributes"
        )
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
                BuildingValidationIssue(
                    code="row_count_mismatch",
                    message="canonical frame row count differs from M1.3 manifest",
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
                    BuildingValidationIssue(
                        code=code,
                        message=f"{count} geometry rows violate {code}",
                        frame=geometry_spec.frame_name,
                        column="geometry_wkb",
                    )
                )
        if bbox is None:
            issues.append(
                BuildingValidationIssue(
                    code="bbox_unavailable",
                    message="no finite building geometry bbox could be calculated",
                    frame=geometry_spec.frame_name,
                )
            )

        crs_valid = (
            canonical_input.geometry_crs == self._schema.canonical_crs
            and canonical_input.geometry_type == "MultiPolygon"
        )
        if not crs_valid:
            issues.append(
                BuildingValidationIssue(
                    code="spatial_metadata_mismatch",
                    message=(
                        f"expected EPSG:5186 MultiPolygon, got "
                        f"{canonical_input.geometry_crs} "
                        f"{canonical_input.geometry_type}"
                    ),
                    frame=geometry_spec.frame_name,
                )
            )

        source_metadata_valid = _source_metadata_matches(
            canonical_input,
            issues,
        )
        forbidden_fields = {
            "observed_area_m2",
            "building_area_m2",
            "model_area",
            "stable_id",
            "source_object_id",
        }
        present_forbidden = forbidden_fields.intersection(
            canonical_input.attribute_table.column_names
            + canonical_input.geometry_table.column_names
        )
        modalities_unjoined = not present_forbidden
        if present_forbidden:
            issues.append(
                BuildingValidationIssue(
                    code="forbidden_derived_field",
                    message=(
                        "M1.4.1 contains forbidden fields: "
                        f"{sorted(present_forbidden)}"
                    ),
                )
            )

        return BuildingValidationResult(
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
            row_counts_valid=row_counts_valid,
            geometry_valid=geometry_valid,
            attributes_valid=attribute_schema_valid,
            crs_valid=crs_valid,
            source_metadata_valid=source_metadata_valid,
            canonical_schema_valid=(
                geometry_schema_valid and attribute_schema_valid
            ),
            modalities_unjoined=modalities_unjoined,
            issues=tuple(issues),
        )
