from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.schema.models import (
    CanonicalRunResult,
    FrameValidationResult,
)
from scene.schema.serialization import (
    CanonicalParquetWriter,
    write_canonical_manifest,
)


def test_canonical_parquet_and_json_manifest_serialization(
    tmp_path: Path,
) -> None:
    schema = pa.schema([pa.field("value", pa.string(), nullable=False)])
    batch = pa.RecordBatch.from_arrays(
        [pa.array(["fixture"])],
        schema=schema,
    )
    parquet_path = tmp_path / "frame.parquet"
    with CanonicalParquetWriter(parquet_path, schema) as writer:
        writer.write_batch(batch)
        writer.commit()

    frame = FrameValidationResult(
        source_name="fixture",
        frame_name="fixture",
        source_kind="tabular",
        row_count=1,
        output_parquet=str(parquet_path),
        output_sha256="b" * 64,
        source_columns_mapped=1,
        canonical_columns=1,
        crs=None,
        geometry_type=None,
        required_fields_valid=True,
        dtypes_valid=True,
        nullable_valid=True,
        crs_valid=True,
        geometry_type_valid=True,
        mapping_succeeded=True,
    )
    result = CanonicalRunResult(
        run_id="20260724_010203_KST",
        schema_name="fixture",
        schema_version="1",
        schema_path="/contract/schema.yaml",
        schema_sha256="a" * 64,
        inventory_path="/inventory.json",
        output_directory=str(tmp_path),
        frames=(frame,),
    )

    artifacts = write_canonical_manifest(result, tmp_path)

    assert pq.read_table(parquet_path).to_pylist() == [{"value": "fixture"}]
    assert (
        pq.ParquetFile(parquet_path).metadata.row_group(0).column(0).compression
        == "ZSTD"
    )
    payload = json.loads(artifacts.manifest_json.read_text(encoding="utf-8"))
    assert payload["mapped_source_count"] == 1
    assert payload["schema_validation_passed"] is True
