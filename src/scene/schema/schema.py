"""Strict loader for the M1.3 canonical schema contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Mapping

import yaml

from scene.schema.exceptions import SchemaDefinitionError
from scene.schema.models import CanonicalColumn, CanonicalFrameSchema, CanonicalSchema
from scene.schema.typing import arrow_type


_FRAME_KEYS = {
    "columns",
    "crs",
    "frame_name",
    "geometry_column",
    "geometry_type",
    "source_kind",
}
_COLUMN_KEYS = {
    "column",
    "description",
    "dtype",
    "nullable",
    "source",
    "source_column",
}
_SOURCE_KINDS = {"raster", "tabular", "vector"}
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise SchemaDefinitionError(f"{context} must be a string-keyed mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaDefinitionError(f"{context} must be a non-empty string")
    return value.strip()


def _load_column(value: object, context: str) -> CanonicalColumn:
    raw = _mapping(value, context)
    missing = _COLUMN_KEYS - set(raw)
    unknown = set(raw) - _COLUMN_KEYS
    if missing or unknown:
        raise SchemaDefinitionError(
            f"{context} keys invalid; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    column = _string(raw["column"], f"{context}.column")
    source_column = _string(raw["source_column"], f"{context}.source_column")
    dtype = _string(raw["dtype"], f"{context}.dtype")
    arrow_type(dtype)
    nullable = raw["nullable"]
    if not isinstance(nullable, bool):
        raise SchemaDefinitionError(f"{context}.nullable must be boolean")
    if _NAME.fullmatch(column) is None:
        raise SchemaDefinitionError(
            f"{context}.column must use lowercase snake_case"
        )
    return CanonicalColumn(
        column=column,
        source_column=source_column,
        dtype=dtype,
        nullable=nullable,
        description=_string(raw["description"], f"{context}.description"),
        source=_string(raw["source"], f"{context}.source"),
    )


def _load_frame(
    source_name: str,
    value: object,
    context: str,
) -> CanonicalFrameSchema:
    raw = _mapping(value, context)
    unknown = set(raw) - _FRAME_KEYS
    required = {"columns", "frame_name", "source_kind"}
    missing = required - set(raw)
    if missing or unknown:
        raise SchemaDefinitionError(
            f"{context} keys invalid; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    source_kind = _string(raw["source_kind"], f"{context}.source_kind").lower()
    if source_kind not in _SOURCE_KINDS:
        raise SchemaDefinitionError(
            f"{context}.source_kind must be one of {sorted(_SOURCE_KINDS)}"
        )
    raw_columns = raw["columns"]
    if not isinstance(raw_columns, list) or not raw_columns:
        raise SchemaDefinitionError(f"{context}.columns must be a non-empty list")
    columns = tuple(
        _load_column(item, f"{context}.columns[{index}]")
        for index, item in enumerate(raw_columns)
    )
    names = [column.column for column in columns]
    if len(names) != len(set(names)):
        raise SchemaDefinitionError(f"{context} has duplicate canonical columns")

    frame_name = _string(raw["frame_name"], f"{context}.frame_name")
    if _NAME.fullmatch(frame_name) is None:
        raise SchemaDefinitionError(
            f"{context}.frame_name must use lowercase snake_case"
        )
    crs = (
        _string(raw["crs"], f"{context}.crs")
        if raw.get("crs") is not None
        else None
    )
    geometry_type = (
        _string(raw["geometry_type"], f"{context}.geometry_type")
        if raw.get("geometry_type") is not None
        else None
    )
    geometry_column = (
        _string(raw["geometry_column"], f"{context}.geometry_column")
        if raw.get("geometry_column") is not None
        else None
    )
    if source_kind == "vector":
        if not crs or not geometry_type or not geometry_column:
            raise SchemaDefinitionError(
                f"{context} vector requires crs, geometry_type, geometry_column"
            )
        if geometry_column not in names:
            raise SchemaDefinitionError(
                f"{context}.geometry_column is not a declared column"
            )
    elif geometry_type is not None or geometry_column is not None:
        raise SchemaDefinitionError(
            f"{context} non-vector cannot declare geometry metadata"
        )
    return CanonicalFrameSchema(
        source_name=source_name,
        frame_name=frame_name,
        source_kind=source_kind,
        columns=columns,
        crs=crs,
        geometry_type=geometry_type,
        geometry_column=geometry_column,
    )


def load_canonical_schema(path: str | Path) -> CanonicalSchema:
    """Load and strictly validate the M1.3 frame section of the YAML contract."""

    schema_path = Path(path).expanduser().resolve(strict=False)
    try:
        content = schema_path.read_bytes()
        raw_document = yaml.safe_load(content)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SchemaDefinitionError(
            f"cannot read canonical schema {schema_path}: {exc}"
        ) from exc
    root = _mapping(raw_document, "canonical schema")
    schema_name = _string(root.get("schema_name"), "schema_name")
    schema_version = _string(root.get("schema_version"), "schema_version")
    raw_crs = _mapping(root.get("canonical_crs"), "canonical_crs")
    epsg = raw_crs.get("epsg")
    if not isinstance(epsg, int) or epsg <= 0:
        raise SchemaDefinitionError("canonical_crs.epsg must be a positive integer")
    raw_frames = _mapping(
        root.get("m1_3_canonical_frames"),
        "m1_3_canonical_frames",
    )
    raw_compatible = root.get(
        "compatible_m1_3_manifest_schema_sha256",
        [],
    )
    if not isinstance(raw_compatible, list) or any(
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in raw_compatible
    ):
        raise SchemaDefinitionError(
            "compatible_m1_3_manifest_schema_sha256 must be a list of SHA-256"
        )
    frames: dict[str, CanonicalFrameSchema] = {}
    frame_names: set[str] = set()
    for source_name, value in raw_frames.items():
        if _NAME.fullmatch(source_name) is None:
            raise SchemaDefinitionError(
                f"invalid M1.3 source name: {source_name}"
            )
        frame = _load_frame(
            source_name,
            value,
            f"m1_3_canonical_frames.{source_name}",
        )
        if frame.frame_name in frame_names:
            raise SchemaDefinitionError(
                f"duplicate M1.3 frame_name: {frame.frame_name}"
            )
        frames[source_name] = frame
        frame_names.add(frame.frame_name)
    return CanonicalSchema(
        schema_name=schema_name,
        schema_version=schema_version,
        canonical_crs=f"EPSG:{epsg}",
        source_frames=frames,
        path=schema_path,
        sha256=hashlib.sha256(content).hexdigest(),
        compatible_manifest_sha256s=tuple(raw_compatible),
    )
