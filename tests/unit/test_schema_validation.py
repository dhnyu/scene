from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from scene.schema.schema import load_canonical_schema
from scene.schema.validator import (
    ValidationAccumulator,
    validate_required_source_fields,
)


def _building_spec():
    root = Path(__file__).resolve().parents[2]
    return load_canonical_schema(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    ).frame_for("seoul_buildings_attributes")


def test_required_source_field_validation_reports_missing() -> None:
    spec = _building_spec()

    missing = validate_required_source_fields({"building_id", "A9"}, spec)

    assert "A11" in missing
    assert "A12" in missing
    assert "A16" in missing
    assert "$row_group_id" not in missing


def test_dtype_validation_detects_mismatch() -> None:
    spec = _building_spec()
    arrays = []
    names = []
    for column in spec.columns:
        names.append(column.column)
        if column.column == "building_height_m":
            arrays.append(pa.array(["wrong"]))
        elif column.dtype == "string":
            arrays.append(pa.array(["value"]))
        elif column.dtype in {"float32", "float64"}:
            arrays.append(pa.array([1.0], type=pa.float64()))
        elif column.dtype == "int32":
            arrays.append(pa.array([1], type=pa.int32()))
        else:
            arrays.append(pa.array([1], type=pa.int64()))
    batch = pa.RecordBatch.from_arrays(arrays, names=names)
    accumulator = ValidationAccumulator(spec=spec, crs=None, geometry_type=None)

    accumulator.validate_batch(batch)

    assert not accumulator.dtypes_valid
    assert any(
        issue.column == "building_height_m"
        and issue.code == "dtype_mismatch"
        for issue in accumulator.issues
    )


def test_nullable_validation_allows_nullable_and_rejects_required_null() -> None:
    spec = _building_spec()
    arrays = []
    schema_fields = []
    for column in spec.columns:
        dtype = {
            "string": pa.string(),
            "float64": pa.float64(),
            "int32": pa.int32(),
            "int64": pa.int64(),
        }[column.dtype]
        value = None if column.column in {"building_use", "source_name"} else (
            1.0 if column.dtype == "float64" else 1
            if column.dtype in {"int32", "int64"}
            else "value"
        )
        arrays.append(pa.array([value], type=dtype))
        schema_fields.append(pa.field(column.column, dtype, nullable=column.nullable))
    batch = pa.RecordBatch.from_arrays(arrays, schema=pa.schema(schema_fields))
    accumulator = ValidationAccumulator(spec=spec, crs=None, geometry_type=None)

    accumulator.validate_batch(batch)

    assert not accumulator.nullable_valid
    assert any(
        issue.column == "source_name" and issue.code == "nullability_violation"
        for issue in accumulator.issues
    )
    assert not any(
        issue.column == "building_use" and issue.code == "nullability_violation"
        for issue in accumulator.issues
    )
