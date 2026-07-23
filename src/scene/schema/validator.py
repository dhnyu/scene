"""Required-field, dtype, nullability, CRS, and geometry validation."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa

from scene.schema.models import (
    CanonicalFrameSchema,
    FrameValidationResult,
    ValidationIssue,
)
from scene.schema.typing import arrow_type


@dataclass(slots=True)
class ValidationAccumulator:
    """Incremental validation state for a streamed canonical frame."""

    spec: CanonicalFrameSchema
    crs: str | None
    geometry_type: str | None
    output_parquet: str | None = None
    output_sha256: str | None = None
    row_count: int = 0
    source_columns_mapped: int = 0
    required_fields_valid: bool = True
    dtypes_valid: bool = True
    nullable_valid: bool = True
    crs_valid: bool = True
    geometry_type_valid: bool = True
    mapping_succeeded: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_issue(
        self,
        code: str,
        message: str,
        *,
        column: str | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(code=code, message=message, column=column)
        )

    def validate_spatial_metadata(self) -> None:
        if self.spec.source_kind not in {"vector", "raster"}:
            return
        if self.crs != self.spec.crs:
            self.crs_valid = False
            self.add_issue(
                "crs_mismatch",
                f"expected {self.spec.crs}, got {self.crs}",
            )
        if self.spec.source_kind == "vector":
            if self.geometry_type != self.spec.geometry_type:
                self.geometry_type_valid = False
                self.add_issue(
                    "geometry_type_mismatch",
                    f"expected {self.spec.geometry_type}, got {self.geometry_type}",
                )

    def validate_batch(self, batch: pa.RecordBatch) -> None:
        expected_names = [column.column for column in self.spec.columns]
        missing = [name for name in expected_names if name not in batch.schema.names]
        if missing:
            self.required_fields_valid = False
            for name in missing:
                self.add_issue(
                    "required_field_missing",
                    f"required canonical column is missing: {name}",
                    column=name,
                )
            return

        self.row_count += batch.num_rows
        for column in self.spec.columns:
            field_value = batch.schema.field(column.column)
            expected_type = arrow_type(column.dtype)
            if field_value.type != expected_type:
                self.dtypes_valid = False
                self.add_issue(
                    "dtype_mismatch",
                    f"expected {expected_type}, got {field_value.type}",
                    column=column.column,
                )
            if not column.nullable:
                null_count = batch.column(
                    batch.schema.get_field_index(column.column)
                ).null_count
                if null_count:
                    self.nullable_valid = False
                    self.add_issue(
                        "nullability_violation",
                        f"non-nullable column contains {null_count} null values",
                        column=column.column,
                    )

    def fail_mapping(self, message: str) -> None:
        self.mapping_succeeded = False
        self.add_issue("mapping_failed", message)

    def result(self) -> FrameValidationResult:
        return FrameValidationResult(
            source_name=self.spec.source_name,
            frame_name=self.spec.frame_name,
            source_kind=self.spec.source_kind,
            row_count=self.row_count,
            output_parquet=self.output_parquet,
            output_sha256=self.output_sha256,
            source_columns_mapped=self.source_columns_mapped,
            canonical_columns=len(self.spec.columns),
            crs=self.crs,
            geometry_type=self.geometry_type,
            required_fields_valid=self.required_fields_valid,
            dtypes_valid=self.dtypes_valid,
            nullable_valid=self.nullable_valid,
            crs_valid=self.crs_valid,
            geometry_type_valid=self.geometry_type_valid,
            mapping_succeeded=self.mapping_succeeded,
            issues=tuple(self.issues),
        )


def validate_required_source_fields(
    available: set[str],
    spec: CanonicalFrameSchema,
) -> tuple[str, ...]:
    """Return required physical source columns absent from a source schema."""

    return tuple(
        column
        for column in spec.required_source_columns
        if column not in available
    )
