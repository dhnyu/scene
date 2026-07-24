"""Read only the validated M1.3 canonical road link and node frames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from scene.inventory.hashing import sha256_file
from scene.roads.dataset import (
    CanonicalRoadInput,
    RoadProvenance,
    RoadSourceMetadata,
)
from scene.roads.exceptions import RoadReaderError
from scene.schema.models import CanonicalSchema


_ROAD_SOURCES = ("seoul_roads_links", "seoul_roads_nodes")


def find_latest_canonical_manifest(output_root: str | Path) -> Path:
    """Find the latest successful M1.3 canonical manifest by run ID."""

    candidates = sorted(
        (Path(output_root) / "canonical").glob("*/*_canonical_manifest.json")
    )
    if not candidates:
        raise RoadReaderError(
            f"no M1.3 canonical manifest found under {Path(output_root)}"
        )
    return candidates[-1]


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RoadReaderError(f"{context} must be a mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RoadReaderError(f"{context} must be a non-empty string")
    return value


def _single_string(table: pa.Table, column: str, context: str) -> str:
    if column not in table.column_names:
        raise RoadReaderError(f"{context} has no {column} column")
    values = pc.unique(table[column]).to_pylist()
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise RoadReaderError(
            f"{context}.{column} must contain exactly one non-null string"
        )
    return values[0]


class RoadReader:
    """Integrity-check and memory-map canonical road Parquet frames."""

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
            raise RoadReaderError(
                f"cannot read canonical manifest {path}: {exc}"
            ) from exc
        manifest = _mapping(payload, "canonical manifest")
        if manifest.get("schema_validation_passed") is not True:
            raise RoadReaderError(
                "canonical manifest did not pass M1.3 schema validation"
            )
        if not self._schema.accepts_manifest(
            schema_name=manifest.get("schema_name"),
            schema_version=manifest.get("schema_version"),
            schema_sha256=manifest.get("schema_sha256"),
        ):
            raise RoadReaderError(
                "canonical manifest schema identity is not compatible"
            )
        return manifest

    def _frame_records(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        raw_frames = manifest.get("frames")
        if not isinstance(raw_frames, list):
            raise RoadReaderError("canonical manifest has no frames list")
        records: dict[str, Mapping[str, Any]] = {}
        for item in raw_frames:
            frame = _mapping(item, "canonical frame")
            source_name = frame.get("source_name")
            if source_name not in _ROAD_SOURCES:
                continue
            if source_name in records:
                raise RoadReaderError(
                    f"duplicate canonical frame: {source_name}"
                )
            if frame.get("valid") is not True:
                raise RoadReaderError(
                    f"canonical frame is invalid: {source_name}"
                )
            records[str(source_name)] = frame
        missing = set(_ROAD_SOURCES) - set(records)
        if missing:
            raise RoadReaderError(
                f"canonical road frames missing: {sorted(missing)}"
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
            raise RoadReaderError(
                f"canonical frame is outside output root: {path}"
            )
        expected_hash = _string(
            record.get("output_sha256"), f"{path.name}.output_sha256"
        )
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise RoadReaderError(f"canonical frame SHA-256 mismatch: {path}")
        try:
            table = pq.read_table(path, memory_map=True)
        except (OSError, pa.ArrowException) as exc:
            raise RoadReaderError(
                f"cannot read canonical frame {path}: {exc}"
            ) from exc
        expected_rows = record.get("row_count")
        if not isinstance(expected_rows, int) or expected_rows < 0:
            raise RoadReaderError(f"invalid manifest row_count for {path}")
        return table, path, actual_hash, expected_rows

    def read(self, manifest_path: str | Path) -> CanonicalRoadInput:
        """Read road frames without joining rows or creating identifiers."""

        path = Path(manifest_path).expanduser().resolve(strict=False)
        manifest = self._load_manifest(path)
        records = self._frame_records(manifest)
        link_table, link_path, link_hash, link_rows = self._read_frame(
            records["seoul_roads_links"]
        )
        node_table, node_path, node_hash, node_rows = self._read_frame(
            records["seoul_roads_nodes"]
        )
        run_id = _string(manifest.get("run_id"), "canonical run_id")
        schema_path = _string(manifest.get("schema_path"), "canonical schema_path")

        def source(table: pa.Table, context: str) -> RoadSourceMetadata:
            return RoadSourceMetadata(
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
        ) -> RoadProvenance:
            spec = self._schema.frame_for(source_name)
            return RoadProvenance(
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

        link_spec = self._schema.frame_for("seoul_roads_links")
        node_spec = self._schema.frame_for("seoul_roads_nodes")
        return CanonicalRoadInput(
            link_table=link_table,
            node_table=node_table,
            link_source=source(link_table, "road link"),
            node_source=source(node_table, "road node"),
            link_provenance=provenance(
                "seoul_roads_links", link_path, link_hash
            ),
            node_provenance=provenance(
                "seoul_roads_nodes", node_path, node_hash
            ),
            link_crs=link_spec.crs or "",
            node_crs=node_spec.crs or "",
            link_geometry_type=link_spec.geometry_type or "",
            node_geometry_type=node_spec.geometry_type or "",
            link_expected_rows=link_rows,
            node_expected_rows=node_rows,
            manifest_path=path,
        )
