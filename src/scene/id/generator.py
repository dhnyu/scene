"""Deterministic stable and future-derived ID factories."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re
from typing import Iterable, Iterator, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from scene.id.exceptions import StableIdGenerationError
from scene.id.provenance import (
    CanonicalIdFrame,
    ENTITY_SPECS,
    EntitySpec,
    StableIdDataset,
    StableIdInput,
)


ID_CONTRACT_VERSION = "m1.5-v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BATCH_SIZE = 65_536


def canonical_bytes(fields: Sequence[str | None]) -> bytes:
    """Serialize nullable UTF-8 fields with explicit type and byte lengths."""

    output = bytearray()
    for field in fields:
        if field is None:
            output.extend(b"N")
            continue
        if not isinstance(field, str):
            raise StableIdGenerationError("canonical hash fields must be strings")
        payload = field.encode("utf-8")
        output.extend(b"S")
        output.extend(len(payload).to_bytes(8, byteorder="big", signed=False))
        output.extend(payload)
    return bytes(output)


def canonical_hash(*fields: str | None) -> str:
    """Return lowercase SHA-256 of the contracted canonical serialization."""

    return hashlib.sha256(canonical_bytes(fields)).hexdigest()


def _native_id(value: object) -> str:
    if not isinstance(value, str) or value == "":
        raise StableIdGenerationError(
            "source native ID must be a non-empty string"
        )
    return value


def source_object_id(
    object_type: str,
    entity_type: str,
    source_native_id: str,
) -> str:
    """Namespace one unmodified source ID into the global registry."""

    if object_type not in {"building", "road", "poi"}:
        raise StableIdGenerationError(f"unsupported object_type: {object_type}")
    allowed = {spec.entity_type for spec in ENTITY_SPECS}
    if entity_type not in allowed:
        raise StableIdGenerationError(f"unsupported entity_type: {entity_type}")
    native = _native_id(source_native_id)
    return canonical_hash(
        "source_object_id",
        object_type,
        entity_type,
        native,
    )


def building_id(source_native_id: str) -> str:
    return source_object_id("building", "building", source_native_id)


def road_link_id(source_native_id: str) -> str:
    return source_object_id("road", "road_link", source_native_id)


def road_node_id(source_native_id: str) -> str:
    return source_object_id("road", "road_node", source_native_id)


def poi_id(source_native_id: str) -> str:
    return source_object_id("poi", "poi", source_native_id)


def district_id(
    source_name: str,
    administrative_level: str,
    district_code: str,
) -> str:
    """Create the stable administrative-boundary ID without geometry inputs."""

    if not source_name or not administrative_level:
        raise StableIdGenerationError(
            "district ID namespace fields must be non-empty"
        )
    return canonical_hash(
        "district_id",
        source_name,
        administrative_level,
        _native_id(district_code),
    )


def _decimal_text(value: int | float | str | Decimal) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise StableIdGenerationError(f"invalid decimal ID input: {value}") from exc
    if not decimal.is_finite():
        raise StableIdGenerationError("ID coordinate inputs must be finite")
    normalized = decimal.normalize()
    text = format(normalized, "f")
    return "0" if text in {"-0", ""} else text


class DerivedIdFactory:
    """Pure factories for contracted IDs that M1.5 does not materialize."""

    @staticmethod
    def scene_footprint_id(
        scene_generation_version: str,
        canonical_crs: str,
        origin_x: int | float | str | Decimal,
        origin_y: int | float | str | Decimal,
        scene_width: int | float | str | Decimal,
        scene_height: int | float | str | Decimal,
        stride_x: int | float | str | Decimal,
        stride_y: int | float | str | Decimal,
        grid_col: int,
        grid_row: int,
    ) -> str:
        if not scene_generation_version:
            raise StableIdGenerationError(
                "scene_generation_version must be non-empty"
            )
        if isinstance(grid_col, bool) or not isinstance(grid_col, int):
            raise StableIdGenerationError("grid_col must be an integer")
        if isinstance(grid_row, bool) or not isinstance(grid_row, int):
            raise StableIdGenerationError("grid_row must be an integer")
        return canonical_hash(
            "scene_footprint_id",
            scene_generation_version,
            canonical_crs,
            _decimal_text(origin_x),
            _decimal_text(origin_y),
            _decimal_text(scene_width),
            _decimal_text(scene_height),
            _decimal_text(stride_x),
            _decimal_text(stride_y),
            str(grid_col),
            str(grid_row),
        )

    @staticmethod
    def scene_id(scene_footprint_id: str) -> str:
        if SHA256_PATTERN.fullmatch(scene_footprint_id) is None:
            raise StableIdGenerationError("invalid scene_footprint_id")
        return scene_footprint_id

    @staticmethod
    def scene_object_id(
        scene_id: str,
        source_geometry_id: str,
        clip_part_id: str,
    ) -> str:
        return canonical_hash(
            "scene_object_id",
            scene_id,
            source_geometry_id,
            clip_part_id,
        )

    @staticmethod
    def clip_part_id(
        geometry_type: str,
        canonical_wkb_sha256: str,
        occurrence_index: int,
    ) -> str:
        if SHA256_PATTERN.fullmatch(canonical_wkb_sha256) is None:
            raise StableIdGenerationError("invalid canonical WKB SHA-256")
        if occurrence_index < 0:
            raise StableIdGenerationError("occurrence_index must be non-negative")
        return canonical_hash(
            "clip_part_id",
            geometry_type,
            canonical_wkb_sha256,
            str(occurrence_index),
        )

    @classmethod
    def clip_part_ids(
        cls,
        components: Iterable[tuple[str, bytes]],
    ) -> tuple[str, ...]:
        keyed = sorted(
            (
                geometry_type,
                hashlib.sha256(wkb).hexdigest(),
            )
            for geometry_type, wkb in components
        )
        occurrences: defaultdict[tuple[str, str], int] = defaultdict(int)
        result: list[str] = []
        for geometry_type, wkb_hash in keyed:
            key = (geometry_type, wkb_hash)
            occurrence = occurrences[key]
            occurrences[key] += 1
            result.append(cls.clip_part_id(geometry_type, wkb_hash, occurrence))
        return tuple(result)

    @staticmethod
    def relation_context_id(
        scene_id: str,
        view_kind: str,
        augmentation_view: int | None,
        geometry_version: str,
    ) -> str:
        if view_kind not in {"original", "augmented"}:
            raise StableIdGenerationError("invalid relation view_kind")
        if view_kind == "original" and augmentation_view is not None:
            raise StableIdGenerationError(
                "original relation context cannot have augmentation_view"
            )
        if view_kind == "augmented" and augmentation_view not in {1, 2}:
            raise StableIdGenerationError(
                "augmented relation context requires view 1 or 2"
            )
        return canonical_hash(
            "relation_context_id",
            scene_id,
            view_kind,
            None if augmentation_view is None else str(augmentation_view),
            geometry_version,
        )

    @staticmethod
    def relation_id(
        relation_context_id: str,
        src_scene_object_id: str,
        dst_scene_object_id: str,
        relation_type: str,
    ) -> str:
        return canonical_hash(
            "relation_id",
            relation_context_id,
            src_scene_object_id,
            dst_scene_object_id,
            relation_type,
        )


def _dictionary(value: str, length: int) -> pa.DictionaryArray:
    return pa.array(
        [value] * length,
        type=pa.dictionary(pa.int8(), pa.string()),
    )


ID_SCHEMA = pa.schema(
    [
        pa.field(
            "entity_type",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "object_type",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "id_name",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field("source_object_id", pa.string(), nullable=False),
        pa.field("canonical_object_id", pa.string(), nullable=False),
        pa.field("source_native_id", pa.string(), nullable=False),
        pa.field(
            "source_native_id_field",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
    ],
    metadata={
        b"scene:id_contract_version": ID_CONTRACT_VERSION.encode("ascii"),
        b"scene:canonical_object_id_aliases_source_object_id": b"true",
    },
)


PROVENANCE_SCHEMA = pa.schema(
    [
        pa.field(
            "entity_type",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "id_name",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field("source_object_id", pa.string(), nullable=False),
        pa.field("canonical_object_id", pa.string(), nullable=False),
        pa.field("source_native_id", pa.string(), nullable=False),
        pa.field(
            "source_native_id_field",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "source_name",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "source_path",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "source_sha256",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "schema_version",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "schema_sha256",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "run_id",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "config_hash",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "canonical_run_id",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "canonical_manifest_path",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "canonical_manifest_sha256",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "canonical_frame_path",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "canonical_frame_sha256",
            pa.dictionary(pa.int8(), pa.string()),
            nullable=False,
        ),
        pa.field(
            "source_row_reference",
            pa.dictionary(pa.int32(), pa.string()),
            nullable=True,
        ),
        pa.field("source_fid", pa.int64(), nullable=True),
    ],
    metadata={
        b"scene:id_contract_version": ID_CONTRACT_VERSION.encode("ascii"),
        b"scene:source_row_reference_is_diagnostic": b"true",
    },
)


def _frame_batches(
    frame: CanonicalIdFrame,
    source: StableIdInput,
    *,
    run_id: str,
    config_hash: str,
) -> Iterator[tuple[pa.RecordBatch, pa.RecordBatch]]:
    columns = [
        frame.spec.source_native_id_field,
        "source_name",
        "source_path",
        "source_file_sha256",
        "source_fid",
    ]
    parquet = pq.ParquetFile(frame.path)
    for batch in parquet.iter_batches(
        batch_size=_BATCH_SIZE,
        columns=columns,
        use_threads=True,
    ):
        native_ids = batch[frame.spec.source_native_id_field].to_pylist()
        stable_ids = [
            source_object_id(
                frame.spec.object_type,
                frame.spec.entity_type,
                _native_id(native),
            )
            for native in native_ids
        ]
        length = len(stable_ids)
        source_names = batch["source_name"].to_pylist()
        source_paths = batch["source_path"].to_pylist()
        source_hashes = batch["source_file_sha256"].to_pylist()
        if not (
            len(set(source_names))
            == len(set(source_paths))
            == len(set(source_hashes))
            == 1
        ):
            raise StableIdGenerationError(
                f"{frame.spec.source_name} source provenance is not constant"
            )
        source_fids = batch["source_fid"]
        row_references = pa.array(
            [
                None if fid is None else f"fid:{fid}"
                for fid in source_fids.to_pylist()
            ],
            type=pa.dictionary(pa.int32(), pa.string()),
        )
        ids = pa.RecordBatch.from_arrays(
            [
                _dictionary(frame.spec.entity_type, length),
                _dictionary(frame.spec.object_type, length),
                _dictionary(frame.spec.id_name, length),
                pa.array(stable_ids, type=pa.string()),
                pa.array(stable_ids, type=pa.string()),
                pa.array(native_ids, type=pa.string()),
                _dictionary(frame.spec.source_native_id_field, length),
            ],
            schema=ID_SCHEMA,
        )
        provenance = pa.RecordBatch.from_arrays(
            [
                _dictionary(frame.spec.entity_type, length),
                _dictionary(frame.spec.id_name, length),
                pa.array(stable_ids, type=pa.string()),
                pa.array(stable_ids, type=pa.string()),
                pa.array(native_ids, type=pa.string()),
                _dictionary(frame.spec.source_native_id_field, length),
                _dictionary(source_names[0], length),
                _dictionary(source_paths[0], length),
                _dictionary(source_hashes[0], length),
                _dictionary(source.schema_version, length),
                _dictionary(source.schema_sha256, length),
                _dictionary(run_id, length),
                _dictionary(config_hash, length),
                _dictionary(source.canonical_run_id, length),
                _dictionary(str(source.canonical_manifest_path), length),
                _dictionary(source.canonical_manifest_sha256, length),
                _dictionary(str(frame.path), length),
                _dictionary(frame.sha256, length),
                row_references,
                source_fids,
            ],
            schema=PROVENANCE_SCHEMA,
        )
        yield ids, provenance


def _update_digest(digest: hashlib._Hash, batch: pa.RecordBatch) -> None:
    for stable_id in batch["canonical_object_id"].to_pylist():
        payload = stable_id.encode("ascii")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)


class StableIdGenerator:
    """Generate four deterministic ID domains from canonical frames."""

    def generate(
        self,
        source: StableIdInput,
        *,
        run_id: str,
        config_hash: str,
    ) -> StableIdDataset:
        id_batches: list[pa.RecordBatch] = []
        provenance_batches: list[pa.RecordBatch] = []
        digest = hashlib.sha256()
        for frame in source.frames:
            for ids, provenance in _frame_batches(
                frame,
                source,
                run_id=run_id,
                config_hash=config_hash,
            ):
                id_batches.append(ids)
                provenance_batches.append(provenance)
                _update_digest(digest, ids)
        return StableIdDataset(
            ids=pa.Table.from_batches(id_batches, schema=ID_SCHEMA),
            provenance=pa.Table.from_batches(
                provenance_batches,
                schema=PROVENANCE_SCHEMA,
            ),
            generation_digest=digest.hexdigest(),
            source=source,
        )

    def regeneration_digest(
        self,
        source: StableIdInput,
        *,
        run_id: str,
        config_hash: str,
    ) -> str:
        digest = hashlib.sha256()
        for frame in source.frames:
            for ids, _ in _frame_batches(
                frame,
                source,
                run_id=run_id,
                config_hash=config_hash,
            ):
                _update_digest(digest, ids)
        return digest.hexdigest()
