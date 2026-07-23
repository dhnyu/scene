"""Atomic Zstandard Parquet and JSON manifest serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.schema.exceptions import CanonicalSerializationError
from scene.schema.models import CanonicalRunResult


@dataclass(frozen=True, slots=True)
class CanonicalArtifactPaths:
    """Top-level M1.3 canonical artifact paths."""

    directory: Path
    manifest_json: Path


class CanonicalParquetWriter:
    """Atomic streamed Parquet writer with explicit commit semantics."""

    def __init__(self, destination: str | Path, schema: pa.Schema) -> None:
        self.path = Path(destination)
        self.temporary = self.path.with_name(f".{self.path.name}.tmp")
        self._schema = schema
        self._writer: pq.ParquetWriter | None = None

    def __enter__(self) -> CanonicalParquetWriter:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = pq.ParquetWriter(
                self.temporary,
                self._schema,
                compression="zstd",
                version="2.6",
            )
        except (OSError, pa.ArrowException) as exc:
            raise CanonicalSerializationError(
                f"cannot open canonical Parquet {self.path}: {exc}"
            ) from exc
        return self

    def write_batch(self, batch: pa.RecordBatch) -> None:
        if self._writer is None:
            raise CanonicalSerializationError("Parquet writer is not open")
        try:
            self._writer.write_batch(batch)
        except pa.ArrowException as exc:
            raise CanonicalSerializationError(
                f"cannot write canonical batch to {self.path}: {exc}"
            ) from exc

    def commit(self) -> Path:
        if self._writer is None:
            raise CanonicalSerializationError("Parquet writer is not open")
        try:
            self._writer.close()
            self._writer = None
            self.temporary.replace(self.path)
        except (OSError, pa.ArrowException) as exc:
            raise CanonicalSerializationError(
                f"cannot commit canonical Parquet {self.path}: {exc}"
            ) from exc
        return self.path

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self.temporary.unlink(missing_ok=True)


def write_canonical_manifest(
    result: CanonicalRunResult,
    directory: str | Path,
) -> CanonicalArtifactPaths:
    """Write the JSON index for heterogeneous canonical Parquet frames."""

    output_dir = Path(directory)
    path = output_dir / f"{result.run_id}_canonical_manifest.json"
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(
                {
                    "canonical_manifest_version": "1.0",
                    **result.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise CanonicalSerializationError(
            f"cannot write canonical JSON manifest {path}: {exc}"
        ) from exc
    return CanonicalArtifactPaths(directory=output_dir, manifest_json=path)
