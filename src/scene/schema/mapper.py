"""Declarative source-column to canonical-column Arrow mapping."""

from __future__ import annotations

from collections.abc import Mapping

import pyarrow as pa
import pyarrow.compute as pc

from scene.schema.exceptions import SourceMappingError
from scene.schema.models import CanonicalFrame, CanonicalFrameSchema
from scene.schema.typing import arrow_type


def canonical_arrow_schema(
    spec: CanonicalFrameSchema,
    *,
    schema_version: str,
    source_sha256: str,
) -> pa.Schema:
    """Build the exact Arrow schema and provenance metadata for a frame."""

    fields = [
        pa.field(
            column.column,
            arrow_type(column.dtype),
            nullable=column.nullable,
            metadata={
                b"description": column.description.encode("utf-8"),
                b"source": column.source.encode("utf-8"),
                b"source_column": column.source_column.encode("utf-8"),
            },
        )
        for column in spec.columns
    ]
    metadata = {
        b"scene:canonical_schema_version": schema_version.encode("utf-8"),
        b"scene:frame_name": spec.frame_name.encode("utf-8"),
        b"scene:source_file_sha256": source_sha256.encode("utf-8"),
        b"scene:source_name": spec.source_name.encode("utf-8"),
    }
    if spec.crs is not None:
        metadata[b"scene:crs"] = spec.crs.encode("utf-8")
    if spec.geometry_type is not None:
        metadata[b"scene:geometry_type"] = spec.geometry_type.encode("utf-8")
    if spec.geometry_column is not None:
        metadata[b"scene:geometry_column"] = spec.geometry_column.encode("utf-8")
    return pa.schema(fields, metadata=metadata)


def _inventory_array(
    key: str,
    inventory: Mapping[str, object],
    length: int,
    target_type: pa.DataType,
) -> pa.Array:
    if key not in inventory or inventory[key] is None:
        raise SourceMappingError(f"inventory field is missing: {key}")
    try:
        return pc.cast(
            pa.repeat(pa.scalar(inventory[key]), length),
            target_type,
            safe=True,
        )
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise SourceMappingError(
            f"inventory field {key} cannot be cast to {target_type}: {exc}"
        ) from exc


def _diagnostic_array(
    name: str,
    batch: pa.RecordBatch,
    length: int,
    *,
    row_group_id: int | None,
    row_offset: int | None,
) -> pa.Array:
    if name == "$source_fid":
        if "fid" not in batch.schema.names:
            raise SourceMappingError("GeoPackage reader did not return source FID")
        return batch.column(batch.schema.get_field_index("fid"))
    if name == "$row_group_id":
        if row_group_id is None:
            raise SourceMappingError("row_group_id is unavailable")
        return pa.repeat(pa.scalar(row_group_id, pa.int32()), length)
    if name == "$row_offset_within_group":
        if row_offset is None:
            raise SourceMappingError("row_offset_within_group is unavailable")
        return pa.array(range(row_offset, row_offset + length), type=pa.int64())
    raise SourceMappingError(f"unsupported diagnostic mapping: {name}")


def map_record_batch(
    batch: pa.RecordBatch,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    *,
    schema_version: str,
    row_group_id: int | None = None,
    row_offset: int | None = None,
) -> pa.RecordBatch:
    """Map one source record batch to its exact canonical Arrow schema."""

    source_sha256 = inventory.get("sha256")
    if not isinstance(source_sha256, str) or not source_sha256:
        raise SourceMappingError("inventory SHA-256 is missing")
    output_schema = canonical_arrow_schema(
        spec,
        schema_version=schema_version,
        source_sha256=source_sha256,
    )
    arrays: list[pa.Array] = []
    for column in spec.columns:
        target_type = arrow_type(column.dtype)
        source_column = column.source_column
        if source_column.startswith("$inventory."):
            array = _inventory_array(
                source_column.removeprefix("$inventory."),
                inventory,
                batch.num_rows,
                target_type,
            )
        elif source_column.startswith("$"):
            array = _diagnostic_array(
                source_column,
                batch,
                batch.num_rows,
                row_group_id=row_group_id,
                row_offset=row_offset,
            )
        else:
            index = batch.schema.get_field_index(source_column)
            if index < 0:
                raise SourceMappingError(
                    f"source column is missing: {source_column}"
                )
            array = batch.column(index)
        try:
            arrays.append(pc.cast(array, target_type, safe=True))
        except (pa.ArrowException, TypeError, ValueError) as exc:
            raise SourceMappingError(
                f"cannot safely map {source_column} to "
                f"{column.column}:{column.dtype}: {exc}"
            ) from exc
    return pa.RecordBatch.from_arrays(arrays, schema=output_schema)


def map_table(
    table: pa.Table,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    *,
    schema_version: str,
    row_group_id: int | None = None,
    row_offset: int | None = None,
) -> CanonicalFrame:
    """Return an in-memory Canonical DataFrame for unit and adapter use."""

    mapped_batches: list[pa.RecordBatch] = []
    next_offset = row_offset
    for batch in table.to_batches():
        mapped_batches.append(
            map_record_batch(
                batch,
                spec,
                inventory,
                schema_version=schema_version,
                row_group_id=row_group_id,
                row_offset=next_offset,
            )
        )
        if next_offset is not None:
            next_offset += batch.num_rows
    if mapped_batches:
        mapped_table = pa.Table.from_batches(mapped_batches)
    else:
        source_sha256 = inventory.get("sha256")
        if not isinstance(source_sha256, str) or not source_sha256:
            raise SourceMappingError("inventory SHA-256 is missing")
        mapped_table = pa.Table.from_batches(
            [],
            schema=canonical_arrow_schema(
                spec,
                schema_version=schema_version,
                source_sha256=source_sha256,
            ),
        )
    return CanonicalFrame(schema=spec, table=mapped_table)
