"""Serialize source-reference raster metadata without raster copies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.inventory.hashing import sha256_file
from scene.raster.exceptions import (
    RasterSerializationError,
    RasterValidationError,
)
from scene.raster.metadata import RasterMetadataCollection, RasterSourceMetadata
from scene.raster.validator import RasterValidationResult


@dataclass(frozen=True, slots=True)
class RasterArtifactPaths:
    """Materialized metadata artifacts and hashes."""

    metadata_json: Path
    metadata_parquet: Path
    json_sha256: str
    parquet_sha256: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {key: str(item) for key, item in value.items()}


_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("source_name", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("source_path", pa.string(), nullable=False),
        pa.field("exists", pa.bool_(), nullable=False),
        pa.field("readable", pa.bool_(), nullable=False),
        pa.field("file_size", pa.int64(), nullable=False),
        pa.field("modified_time_kst", pa.string(), nullable=False),
        pa.field("modified_time_ns", pa.int64(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
        pa.field("driver", pa.string(), nullable=False),
        pa.field("crs", pa.string(), nullable=False),
        pa.field("width", pa.int64(), nullable=False),
        pa.field("height", pa.int64(), nullable=False),
        pa.field("resolution_x", pa.float64(), nullable=False),
        pa.field("resolution_y", pa.float64(), nullable=False),
        pa.field("extent_min_x", pa.float64(), nullable=False),
        pa.field("extent_min_y", pa.float64(), nullable=False),
        pa.field("extent_max_x", pa.float64(), nullable=False),
        pa.field("extent_max_y", pa.float64(), nullable=False),
        pa.field("transform_0", pa.float64(), nullable=False),
        pa.field("transform_1", pa.float64(), nullable=False),
        pa.field("transform_2", pa.float64(), nullable=False),
        pa.field("transform_3", pa.float64(), nullable=False),
        pa.field("transform_4", pa.float64(), nullable=False),
        pa.field("transform_5", pa.float64(), nullable=False),
        pa.field("band_count", pa.int32(), nullable=False),
        pa.field("dtype", pa.string(), nullable=False),
        pa.field("nodata", pa.string(), nullable=False),
        pa.field("compression", pa.string(), nullable=True),
        pa.field("color_table_present", pa.bool_(), nullable=False),
        pa.field("color_interpretation", pa.string(), nullable=True),
        pa.field("source_reference_only", pa.bool_(), nullable=False),
        pa.field("pixel_data_read", pa.bool_(), nullable=False),
        pa.field("pixel_data_copied", pa.bool_(), nullable=False),
        pa.field("source_values_modified", pa.bool_(), nullable=False),
    ],
    metadata={
        b"scene:adapter": b"M1.4.4",
        b"scene:pixel_storage": b"forbidden",
        b"scene:source_policy": b"read_only_reference",
    },
)


def _parquet_row(source: RasterSourceMetadata) -> dict[str, object]:
    if (
        source.resolution is None
        or source.extent is None
        or source.affine_transform is None
    ):
        raise RasterSerializationError(
            f"incomplete spatial metadata for {source.source_name}"
        )
    row = source.to_dict()
    row.pop("resolution")
    row.pop("extent")
    row.pop("affine_transform")
    row.update(
        {
            "resolution_x": source.resolution[0],
            "resolution_y": source.resolution[1],
            "extent_min_x": source.extent[0],
            "extent_min_y": source.extent[1],
            "extent_max_x": source.extent[2],
            "extent_max_y": source.extent[3],
            **{
                f"transform_{index}": value
                for index, value in enumerate(source.affine_transform)
            },
        }
    )
    return row


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_parquet(path: Path, table: pa.Table) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    pq.write_table(
        table,
        temporary,
        compression="zstd",
        version="2.6",
    )
    temporary.replace(path)


class RasterSerializer:
    """Write JSON and Parquet metadata, never a raster payload."""

    def serialize(
        self,
        collection: RasterMetadataCollection,
        validation: RasterValidationResult,
        output_directory: str | Path,
        *,
        run_id: str,
    ) -> RasterArtifactPaths:
        if not validation.valid:
            raise RasterValidationError(
                "invalid raster metadata cannot be serialized"
            )
        directory = Path(output_directory)
        json_path = directory / "raster_metadata.json"
        parquet_path = directory / "raster_metadata.parquet"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "adapter_version": "M1.4.4",
                "collection": collection.to_dict(),
                "pixel_artifacts_created": False,
                "run_id": run_id,
                "source_raster_copies_created": False,
                "validation": validation.to_dict(),
            }
            _atomic_json(json_path, payload)
            table = pa.Table.from_pylist(
                [
                    _parquet_row(collection.landcover),
                    _parquet_row(collection.dem),
                ],
                schema=_PARQUET_SCHEMA,
            )
            _atomic_parquet(parquet_path, table)
            json_hash = sha256_file(json_path)
            parquet_hash = sha256_file(parquet_path)
        except (OSError, TypeError, ValueError, pa.ArrowException) as exc:
            raise RasterSerializationError(
                f"cannot serialize raster metadata: {exc}"
            ) from exc
        return RasterArtifactPaths(
            metadata_json=json_path,
            metadata_parquet=parquet_path,
            json_sha256=json_hash,
            parquet_sha256=parquet_hash,
        )
