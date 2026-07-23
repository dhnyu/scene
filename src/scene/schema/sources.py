"""Stream registered vector, tabular, and raster sources into canonical frames."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.core.config import SourceConfig
from scene.inventory.hashing import sha256_file
from scene.schema.exceptions import CanonicalSchemaError, SourceMappingError
from scene.schema.mapper import canonical_arrow_schema, map_record_batch
from scene.schema.models import CanonicalFrameSchema, FrameValidationResult
from scene.schema.serialization import CanonicalParquetWriter
from scene.schema.validator import (
    ValidationAccumulator,
    validate_required_source_fields,
)


def _validate_inventory_identity(
    source: SourceConfig,
    inventory: Mapping[str, object],
) -> None:
    current_hash = sha256_file(source.path)
    if current_hash != inventory.get("sha256"):
        raise SourceMappingError(
            f"source SHA-256 differs from M1.2 inventory: {source.source_name}"
        )


def _output_schema(
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    schema_version: str,
) -> pa.Schema:
    sha256 = inventory.get("sha256")
    if not isinstance(sha256, str):
        raise SourceMappingError("inventory SHA-256 is missing")
    return canonical_arrow_schema(
        spec,
        schema_version=schema_version,
        source_sha256=sha256,
    )


def _map_vector(
    source: SourceConfig,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    destination: Path,
    schema_version: str,
) -> FrameValidationResult:
    info = pyogrio.read_info(source.path, layer=source.layer)
    crs = str(info.get("crs")) if info.get("crs") is not None else None
    geometry_type = (
        str(info.get("geometry_type"))
        if info.get("geometry_type") is not None
        else None
    )
    accumulator = ValidationAccumulator(
        spec=spec,
        crs=crs,
        geometry_type=geometry_type,
    )
    accumulator.validate_spatial_metadata()
    available = {str(field) for field in info.get("fields", ())}
    geometry_name = str(info.get("geometry_name") or "")
    if geometry_name:
        available.add(geometry_name)
    missing = validate_required_source_fields(available, spec)
    if missing:
        accumulator.required_fields_valid = False
        accumulator.fail_mapping(
            f"required source columns are missing: {', '.join(missing)}"
        )
        return accumulator.result()

    physical_columns = [
        column
        for column in spec.required_source_columns
        if column != geometry_name
    ]
    accumulator.source_columns_mapped = len(spec.required_source_columns)
    try:
        with pyogrio.open_arrow(
            source.path,
            layer=source.layer,
            columns=physical_columns,
            read_geometry=True,
            return_fids=True,
            batch_size=65536,
            use_pyarrow=True,
        ) as (_, reader):
            with CanonicalParquetWriter(
                destination,
                _output_schema(spec, inventory, schema_version),
            ) as writer:
                for source_batch in reader:
                    canonical_batch = map_record_batch(
                        source_batch,
                        spec,
                        inventory,
                        schema_version=schema_version,
                    )
                    accumulator.validate_batch(canonical_batch)
                    writer.write_batch(canonical_batch)
                if not accumulator.issues:
                    writer.commit()
    except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
        accumulator.fail_mapping(str(exc))
    if accumulator.issues:
        destination.unlink(missing_ok=True)
    else:
        accumulator.output_parquet = str(destination)
        accumulator.output_sha256 = sha256_file(destination)
    return accumulator.result()


def _map_tabular(
    source: SourceConfig,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    destination: Path,
    schema_version: str,
) -> FrameValidationResult:
    accumulator = ValidationAccumulator(
        spec=spec,
        crs=None,
        geometry_type=None,
    )
    try:
        parquet = pq.ParquetFile(source.path)
        available = set(parquet.schema_arrow.names)
        missing = validate_required_source_fields(available, spec)
        if missing:
            accumulator.required_fields_valid = False
            accumulator.fail_mapping(
                f"required source columns are missing: {', '.join(missing)}"
            )
            return accumulator.result()

        physical_columns = list(spec.required_source_columns)
        accumulator.source_columns_mapped = len(physical_columns)
        with CanonicalParquetWriter(
            destination,
            _output_schema(spec, inventory, schema_version),
        ) as writer:
            for row_group_id in range(parquet.metadata.num_row_groups):
                row_offset = 0
                for source_batch in parquet.iter_batches(
                    batch_size=65536,
                    row_groups=[row_group_id],
                    columns=physical_columns,
                ):
                    canonical_batch = map_record_batch(
                        source_batch,
                        spec,
                        inventory,
                        schema_version=schema_version,
                        row_group_id=row_group_id,
                        row_offset=row_offset,
                    )
                    row_offset += source_batch.num_rows
                    accumulator.validate_batch(canonical_batch)
                    writer.write_batch(canonical_batch)
            if not accumulator.issues:
                writer.commit()
    except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
        accumulator.fail_mapping(str(exc))
    if accumulator.issues:
        destination.unlink(missing_ok=True)
    else:
        accumulator.output_parquet = str(destination)
        accumulator.output_sha256 = sha256_file(destination)
    return accumulator.result()


def _map_raster(
    source: SourceConfig,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    destination: Path,
    schema_version: str,
) -> FrameValidationResult:
    crs = str(inventory.get("crs")) if inventory.get("crs") is not None else None
    accumulator = ValidationAccumulator(
        spec=spec,
        crs=crs,
        geometry_type=None,
    )
    accumulator.validate_spatial_metadata()
    accumulator.source_columns_mapped = len(spec.required_source_columns)
    dummy = pa.RecordBatch.from_arrays(
        [pa.array([0], type=pa.int8())],
        names=["__raster_metadata_row__"],
    )
    try:
        canonical_batch = map_record_batch(
            dummy,
            spec,
            inventory,
            schema_version=schema_version,
        )
        accumulator.validate_batch(canonical_batch)
        if not accumulator.issues:
            with CanonicalParquetWriter(
                destination,
                canonical_batch.schema,
            ) as writer:
                writer.write_batch(canonical_batch)
                writer.commit()
    except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
        accumulator.fail_mapping(str(exc))
    if accumulator.issues:
        destination.unlink(missing_ok=True)
    else:
        accumulator.output_parquet = str(destination)
        accumulator.output_sha256 = sha256_file(destination)
    return accumulator.result()


def map_source(
    source: SourceConfig,
    spec: CanonicalFrameSchema,
    inventory: Mapping[str, object],
    destination: str | Path,
    *,
    schema_version: str,
) -> FrameValidationResult:
    """Map and validate one registered source without mutating it."""

    output_path = Path(destination)
    try:
        _validate_inventory_identity(source, inventory)
        if source.kind != spec.source_kind:
            raise SourceMappingError(
                f"source kind mismatch: config={source.kind}, schema={spec.source_kind}"
            )
        if source.kind == "vector":
            return _map_vector(
                source, spec, inventory, output_path, schema_version
            )
        if source.kind == "tabular":
            return _map_tabular(
                source, spec, inventory, output_path, schema_version
            )
        return _map_raster(
            source, spec, inventory, output_path, schema_version
        )
    except (
        CanonicalSchemaError,
        OSError,
        ValueError,
        RuntimeError,
        pa.ArrowException,
    ) as exc:
        accumulator = ValidationAccumulator(
            spec=spec,
            crs=(
                str(inventory.get("crs"))
                if inventory.get("crs") is not None
                else None
            ),
            geometry_type=(
                str(inventory.get("geometry_type"))
                if inventory.get("geometry_type") is not None
                else None
            ),
        )
        accumulator.fail_mapping(str(exc))
        output_path.unlink(missing_ok=True)
        return accumulator.result()
