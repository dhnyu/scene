"""Read only the two validated M1.3 canonical building frames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from scene.buildings.dataset import (
    BuildingProvenance,
    BuildingSourceMetadata,
    CanonicalBuildingInput,
)
from scene.buildings.exceptions import BuildingReaderError
from scene.inventory.hashing import sha256_file
from scene.schema.models import CanonicalSchema


_BUILDING_SOURCES = (
    "seoul_buildings_geometry",
    "seoul_buildings_attributes",
)


def find_latest_canonical_manifest(output_root: str | Path) -> Path:
    """Find the latest successful M1.3 canonical manifest by run ID."""

    candidates = sorted(
        (Path(output_root) / "canonical").glob(
            "*/*_canonical_manifest.json"
        )
    )
    if not candidates:
        raise BuildingReaderError(
            f"no M1.3 canonical manifest found under {Path(output_root)}"
        )
    return candidates[-1]


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BuildingReaderError(f"{context} must be a mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise BuildingReaderError(f"{context} must be a non-empty string")
    return value


def _single_string(
    table: pa.Table,
    column: str,
    context: str,
) -> str:
    if column not in table.column_names:
        raise BuildingReaderError(f"{context} has no {column} column")
    values = pc.unique(table[column]).to_pylist()
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise BuildingReaderError(
            f"{context}.{column} must contain exactly one non-null string"
        )
    return values[0]


class BuildingReader:
    """Integrity-check and memory-map canonical building Parquet frames."""

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
            raise BuildingReaderError(
                f"cannot read canonical manifest {path}: {exc}"
            ) from exc
        manifest = _mapping(payload, "canonical manifest")
        if manifest.get("schema_validation_passed") is not True:
            raise BuildingReaderError(
                "canonical manifest did not pass M1.3 schema validation"
            )
        if not self._schema.accepts_manifest(
            schema_name=manifest.get("schema_name"),
            schema_version=manifest.get("schema_version"),
            schema_sha256=manifest.get("schema_sha256"),
        ):
            raise BuildingReaderError(
                "canonical manifest schema identity is not compatible"
            )
        return manifest

    def _frame_records(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        raw_frames = manifest.get("frames")
        if not isinstance(raw_frames, list):
            raise BuildingReaderError("canonical manifest has no frames list")
        records: dict[str, Mapping[str, Any]] = {}
        for item in raw_frames:
            frame = _mapping(item, "canonical frame")
            source_name = frame.get("source_name")
            if source_name in _BUILDING_SOURCES:
                if source_name in records:
                    raise BuildingReaderError(
                        f"duplicate canonical frame: {source_name}"
                    )
                if frame.get("valid") is not True:
                    raise BuildingReaderError(
                        f"canonical frame is invalid: {source_name}"
                    )
                records[str(source_name)] = frame
        missing = set(_BUILDING_SOURCES) - set(records)
        if missing:
            raise BuildingReaderError(
                f"canonical building frames missing: {sorted(missing)}"
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
            raise BuildingReaderError(
                f"canonical frame is outside output root: {path}"
            )
        expected_hash = _string(
            record.get("output_sha256"),
            f"{path.name}.output_sha256",
        )
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise BuildingReaderError(
                f"canonical frame SHA-256 mismatch: {path}"
            )
        try:
            table = pq.read_table(path, memory_map=True)
        except (OSError, pa.ArrowException) as exc:
            raise BuildingReaderError(
                f"cannot read canonical frame {path}: {exc}"
            ) from exc
        expected_rows = record.get("row_count")
        if not isinstance(expected_rows, int) or expected_rows < 0:
            raise BuildingReaderError(f"invalid manifest row_count for {path}")
        return table, path, actual_hash, expected_rows

    def read(self, manifest_path: str | Path) -> CanonicalBuildingInput:
        """Read building frames without joining rows or creating identifiers."""

        path = Path(manifest_path).expanduser().resolve(strict=False)
        manifest = self._load_manifest(path)
        records = self._frame_records(manifest)
        geometry_table, geometry_path, geometry_hash, geometry_rows = (
            self._read_frame(records["seoul_buildings_geometry"])
        )
        attribute_table, attribute_path, attribute_hash, attribute_rows = (
            self._read_frame(records["seoul_buildings_attributes"])
        )
        run_id = _string(manifest.get("run_id"), "canonical run_id")
        schema_path = _string(
            manifest.get("schema_path"),
            "canonical schema_path",
        )

        geometry_source = BuildingSourceMetadata(
            source_name=_single_string(
                geometry_table, "source_name", "building geometry"
            ),
            source_path=_single_string(
                geometry_table, "source_path", "building geometry"
            ),
            source_file_sha256=_single_string(
                geometry_table,
                "source_file_sha256",
                "building geometry",
            ),
        )
        attribute_source = BuildingSourceMetadata(
            source_name=_single_string(
                attribute_table, "source_name", "building attribute"
            ),
            source_path=_single_string(
                attribute_table, "source_path", "building attribute"
            ),
            source_file_sha256=_single_string(
                attribute_table,
                "source_file_sha256",
                "building attribute",
            ),
        )

        def provenance(
            source_name: str,
            frame_path: Path,
            frame_hash: str,
        ) -> BuildingProvenance:
            spec = self._schema.frame_for(source_name)
            return BuildingProvenance(
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

        geometry_spec = self._schema.frame_for("seoul_buildings_geometry")
        return CanonicalBuildingInput(
            geometry_table=geometry_table,
            attribute_table=attribute_table,
            geometry_source=geometry_source,
            attribute_source=attribute_source,
            geometry_provenance=provenance(
                "seoul_buildings_geometry",
                geometry_path,
                geometry_hash,
            ),
            attribute_provenance=provenance(
                "seoul_buildings_attributes",
                attribute_path,
                attribute_hash,
            ),
            geometry_crs=geometry_spec.crs or "",
            geometry_type=geometry_spec.geometry_type or "",
            geometry_expected_rows=geometry_rows,
            attribute_expected_rows=attribute_rows,
            manifest_path=path,
        )
