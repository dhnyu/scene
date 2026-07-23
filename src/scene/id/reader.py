"""Read and integrity-check M1.3 canonical ID inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

from scene.id.exceptions import StableIdReaderError
from scene.id.provenance import (
    CanonicalIdFrame,
    ENTITY_SPECS,
    StableIdInput,
)
from scene.inventory.hashing import sha256_file
from scene.schema.models import CanonicalSchema


def find_latest_canonical_manifest(output_root: str | Path) -> Path:
    """Find the latest successful M1.3 manifest by run ID."""

    candidates = sorted(
        (Path(output_root) / "canonical").glob("*/*_canonical_manifest.json")
    )
    if not candidates:
        raise StableIdReaderError(
            f"no M1.3 canonical manifest found under {Path(output_root)}"
        )
    return candidates[-1]


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StableIdReaderError(f"{context} must be a mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise StableIdReaderError(f"{context} must be a non-empty string")
    return value


class StableIdReader:
    """Validate the manifest and select only four canonical geometry frames."""

    def __init__(
        self,
        schema: CanonicalSchema,
        canonical_output_root: str | Path,
    ) -> None:
        self._schema = schema
        self._canonical_output_root = (
            Path(canonical_output_root) / "canonical"
        ).resolve(strict=False)

    def _load_manifest(self, path: Path) -> Mapping[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StableIdReaderError(
                f"cannot read canonical manifest {path}: {exc}"
            ) from exc
        manifest = _mapping(payload, "canonical manifest")
        if manifest.get("schema_validation_passed") is not True:
            raise StableIdReaderError(
                "canonical manifest did not pass M1.3 schema validation"
            )
        expected = (
            ("schema_name", self._schema.schema_name),
            ("schema_version", self._schema.schema_version),
            ("schema_sha256", self._schema.sha256),
        )
        for key, expected_value in expected:
            if manifest.get(key) != expected_value:
                raise StableIdReaderError(f"canonical {key} mismatch")
        return manifest

    def _frame_records(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        frames = manifest.get("frames")
        if not isinstance(frames, list):
            raise StableIdReaderError("canonical manifest has no frames list")
        required = {spec.source_name for spec in ENTITY_SPECS}
        records: dict[str, Mapping[str, Any]] = {}
        for value in frames:
            record = _mapping(value, "canonical frame")
            source_name = record.get("source_name")
            if source_name not in required:
                continue
            if source_name in records:
                raise StableIdReaderError(
                    f"duplicate canonical frame: {source_name}"
                )
            if record.get("valid") is not True:
                raise StableIdReaderError(
                    f"canonical frame is invalid: {source_name}"
                )
            records[str(source_name)] = record
        missing = required - set(records)
        if missing:
            raise StableIdReaderError(
                f"canonical ID frames missing: {sorted(missing)}"
            )
        return records

    def _read_frame(
        self,
        record: Mapping[str, Any],
        source_name: str,
    ) -> CanonicalIdFrame:
        spec = next(
            item for item in ENTITY_SPECS if item.source_name == source_name
        )
        path = Path(
            _string(record.get("output_parquet"), "output_parquet")
        ).resolve(strict=False)
        if not path.is_relative_to(self._canonical_output_root):
            raise StableIdReaderError(
                f"canonical frame is outside output root: {path}"
            )
        expected_hash = _string(
            record.get("output_sha256"),
            f"{path.name}.output_sha256",
        )
        try:
            actual_hash = sha256_file(path)
        except OSError as exc:
            raise StableIdReaderError(
                f"cannot hash canonical frame {path}: {exc}"
            ) from exc
        if actual_hash != expected_hash:
            raise StableIdReaderError(
                f"canonical frame SHA-256 mismatch: {path}"
            )
        expected_rows = record.get("row_count")
        if not isinstance(expected_rows, int) or expected_rows < 0:
            raise StableIdReaderError(f"invalid manifest row_count for {path}")
        try:
            parquet = pq.ParquetFile(path)
        except (OSError, pa.ArrowException) as exc:
            raise StableIdReaderError(
                f"cannot read canonical frame {path}: {exc}"
            ) from exc
        required_columns = {
            "source_name",
            "source_path",
            "source_file_sha256",
            "source_fid",
            spec.source_native_id_field,
        }
        missing = required_columns - set(parquet.schema_arrow.names)
        if missing:
            raise StableIdReaderError(
                f"{source_name} missing ID columns: {sorted(missing)}"
            )
        if parquet.metadata.num_rows != expected_rows:
            raise StableIdReaderError(
                f"{source_name} row count differs from canonical manifest"
            )
        native_field = parquet.schema_arrow.field(spec.source_native_id_field)
        if not pa.types.is_string(native_field.type) or native_field.nullable:
            raise StableIdReaderError(
                f"{source_name}.{spec.source_native_id_field} "
                "must be non-null string"
            )
        return CanonicalIdFrame(
            spec=spec,
            path=path,
            sha256=actual_hash,
            row_count=expected_rows,
        )

    def read(self, manifest_path: str | Path) -> StableIdInput:
        """Return integrity-checked references without loading geometry."""

        path = Path(manifest_path).expanduser().resolve(strict=False)
        manifest = self._load_manifest(path)
        records = self._frame_records(manifest)
        frames = tuple(
            self._read_frame(records[spec.source_name], spec.source_name)
            for spec in ENTITY_SPECS
        )
        return StableIdInput(
            canonical_manifest_path=path,
            canonical_manifest_sha256=sha256_file(path),
            canonical_run_id=_string(
                manifest.get("run_id"),
                "canonical run_id",
            ),
            schema_name=self._schema.schema_name,
            schema_version=self._schema.schema_version,
            schema_sha256=self._schema.sha256,
            frames=frames,
        )
