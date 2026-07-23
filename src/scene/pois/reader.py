"""Read only the validated M1.3 canonical POI frames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from scene.inventory.hashing import sha256_file
from scene.pois.dataset import (
    CanonicalPOIInput,
    POIProvenance,
    POISourceMetadata,
)
from scene.pois.exceptions import POIReaderError
from scene.schema.models import CanonicalSchema


_POI_SOURCES = ("seoul_poi_geometry", "seoul_poi_attributes")


def find_latest_canonical_manifest(output_root: str | Path) -> Path:
    candidates = sorted(
        (Path(output_root) / "canonical").glob("*/*_canonical_manifest.json")
    )
    if not candidates:
        raise POIReaderError(
            f"no M1.3 canonical manifest found under {Path(output_root)}"
        )
    return candidates[-1]


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise POIReaderError(f"{context} must be a mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise POIReaderError(f"{context} must be a non-empty string")
    return value


def _single_string(table: pa.Table, column: str, context: str) -> str:
    if column not in table.column_names:
        raise POIReaderError(f"{context} has no {column} column")
    values = pc.unique(table[column]).to_pylist()
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise POIReaderError(
            f"{context}.{column} must contain exactly one non-null string"
        )
    return values[0]


class POIReader:
    """Integrity-check and memory-map canonical POI Parquet frames."""

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
            raise POIReaderError(
                f"cannot read canonical manifest {path}: {exc}"
            ) from exc
        manifest = _mapping(payload, "canonical manifest")
        if manifest.get("schema_validation_passed") is not True:
            raise POIReaderError(
                "canonical manifest did not pass M1.3 schema validation"
            )
        expected = (
            ("schema_name", self._schema.schema_name),
            ("schema_version", self._schema.schema_version),
            ("schema_sha256", self._schema.sha256),
        )
        for key, value in expected:
            if manifest.get(key) != value:
                raise POIReaderError(f"canonical {key} mismatch")
        return manifest

    def _frame_records(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        raw_frames = manifest.get("frames")
        if not isinstance(raw_frames, list):
            raise POIReaderError("canonical manifest has no frames list")
        records: dict[str, Mapping[str, Any]] = {}
        for item in raw_frames:
            frame = _mapping(item, "canonical frame")
            source_name = frame.get("source_name")
            if source_name not in _POI_SOURCES:
                continue
            if source_name in records:
                raise POIReaderError(
                    f"duplicate canonical frame: {source_name}"
                )
            if frame.get("valid") is not True:
                raise POIReaderError(
                    f"canonical frame is invalid: {source_name}"
                )
            records[str(source_name)] = frame
        missing = set(_POI_SOURCES) - set(records)
        if missing:
            raise POIReaderError(
                f"canonical POI frames missing: {sorted(missing)}"
            )
        return records

    def _read_frame(
        self,
        record: Mapping[str, Any],
    ) -> tuple[pa.Table, Path, str, int]:
        path = Path(
            _string(record.get("output_parquet"), "output_parquet")
        ).resolve(strict=False)
        if not path.is_relative_to(self._canonical_output_root):
            raise POIReaderError(
                f"canonical frame is outside output root: {path}"
            )
        expected_hash = _string(
            record.get("output_sha256"), f"{path.name}.output_sha256"
        )
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise POIReaderError(f"canonical frame SHA-256 mismatch: {path}")
        try:
            table = pq.read_table(path, memory_map=True)
        except (OSError, pa.ArrowException) as exc:
            raise POIReaderError(
                f"cannot read canonical frame {path}: {exc}"
            ) from exc
        expected_rows = record.get("row_count")
        if not isinstance(expected_rows, int) or expected_rows < 0:
            raise POIReaderError(f"invalid manifest row_count for {path}")
        return table, path, actual_hash, expected_rows

    def read(self, manifest_path: str | Path) -> CanonicalPOIInput:
        """Read POI frames without joining, deduplicating, or adding IDs."""

        path = Path(manifest_path).expanduser().resolve(strict=False)
        manifest = self._load_manifest(path)
        records = self._frame_records(manifest)
        geometry_table, geometry_path, geometry_hash, geometry_rows = (
            self._read_frame(records["seoul_poi_geometry"])
        )
        attribute_table, attribute_path, attribute_hash, attribute_rows = (
            self._read_frame(records["seoul_poi_attributes"])
        )
        run_id = _string(manifest.get("run_id"), "canonical run_id")
        schema_path = _string(manifest.get("schema_path"), "canonical schema_path")

        def source(table: pa.Table, context: str) -> POISourceMetadata:
            return POISourceMetadata(
                source_name=_single_string(table, "source_name", context),
                source_path=_single_string(table, "source_path", context),
                source_file_sha256=_single_string(
                    table, "source_file_sha256", context
                ),
            )

        def provenance(
            source_name: str,
            frame_path: Path,
            frame_hash: str,
        ) -> POIProvenance:
            spec = self._schema.frame_for(source_name)
            return POIProvenance(
                canonical_run_id=run_id,
                canonical_manifest_path=str(path),
                canonical_schema_name=self._schema.schema_name,
                canonical_schema_version=self._schema.schema_version,
                canonical_schema_path=schema_path,
                canonical_schema_sha256=self._schema.sha256,
                frame_name=spec.frame_name,
                canonical_frame_path=str(frame_path),
                canonical_frame_sha256=frame_hash,
            )

        geometry_spec = self._schema.frame_for("seoul_poi_geometry")
        return CanonicalPOIInput(
            geometry_table=geometry_table,
            attribute_table=attribute_table,
            geometry_source=source(geometry_table, "POI geometry"),
            attribute_source=source(attribute_table, "POI attribute"),
            geometry_provenance=provenance(
                "seoul_poi_geometry", geometry_path, geometry_hash
            ),
            attribute_provenance=provenance(
                "seoul_poi_attributes", attribute_path, attribute_hash
            ),
            geometry_crs=geometry_spec.crs or "",
            geometry_type=geometry_spec.geometry_type or "",
            geometry_expected_rows=geometry_rows,
            attribute_expected_rows=attribute_rows,
            manifest_path=path,
        )
