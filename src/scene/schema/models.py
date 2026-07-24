"""Typed canonical schema, frame, and validation records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class CanonicalColumn:
    """One declared source-to-canonical column mapping."""

    column: str
    source_column: str
    dtype: str
    nullable: bool
    description: str
    source: str


@dataclass(frozen=True, slots=True)
class CanonicalFrameSchema:
    """One pre-ID M1.3 canonical frame definition."""

    source_name: str
    frame_name: str
    source_kind: str
    columns: tuple[CanonicalColumn, ...]
    crs: str | None = None
    geometry_type: str | None = None
    geometry_column: str | None = None

    @property
    def required_source_columns(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                column.source_column
                for column in self.columns
                if not column.source_column.startswith("$")
            )
        )


@dataclass(frozen=True, slots=True)
class CanonicalSchema:
    """Validated M1.3 section of the canonical schema contract."""

    schema_name: str
    schema_version: str
    canonical_crs: str
    source_frames: dict[str, CanonicalFrameSchema]
    path: Path
    sha256: str
    compatible_manifest_sha256s: tuple[str, ...] = ()

    def frame_for(self, source_name: str) -> CanonicalFrameSchema:
        try:
            return self.source_frames[source_name]
        except KeyError as exc:
            from scene.schema.exceptions import SchemaDefinitionError

            raise SchemaDefinitionError(
                f"no M1.3 canonical frame for source: {source_name}"
            ) from exc

    def accepts_manifest(
        self,
        *,
        schema_name: object,
        schema_version: object,
        schema_sha256: object,
    ) -> bool:
        return (
            schema_name == self.schema_name
            and schema_version == self.schema_version
            and isinstance(schema_sha256, str)
            and schema_sha256
            in {self.sha256, *self.compatible_manifest_sha256s}
        )


@dataclass(frozen=True, slots=True)
class CanonicalFrame:
    """An in-memory Arrow DataFrame plus spatial metadata."""

    schema: CanonicalFrameSchema
    table: pa.Table


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One source mapping or schema violation."""

    code: str
    message: str
    column: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrameValidationResult:
    """Validation and serialization outcome for one registered source."""

    source_name: str
    frame_name: str
    source_kind: str
    row_count: int
    output_parquet: str | None
    output_sha256: str | None
    source_columns_mapped: int
    canonical_columns: int
    crs: str | None
    geometry_type: str | None
    required_fields_valid: bool
    dtypes_valid: bool
    nullable_valid: bool
    crs_valid: bool
    geometry_type_valid: bool
    mapping_succeeded: bool
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return (
            self.mapping_succeeded
            and self.required_fields_valid
            and self.dtypes_valid
            and self.nullable_valid
            and self.crs_valid
            and self.geometry_type_valid
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issues"] = [issue.to_dict() for issue in self.issues]
        value["valid"] = self.valid
        return value


@dataclass(frozen=True, slots=True)
class CanonicalRunResult:
    """Complete M1.3 mapping outcome."""

    run_id: str
    schema_name: str
    schema_version: str
    schema_path: str
    schema_sha256: str
    inventory_path: str
    output_directory: str
    frames: tuple[FrameValidationResult, ...]

    @property
    def source_count(self) -> int:
        return len(self.frames)

    @property
    def mapped_source_count(self) -> int:
        return sum(frame.mapping_succeeded for frame in self.frames)

    @property
    def failure_count(self) -> int:
        return sum(not frame.valid for frame in self.frames)

    @property
    def schema_validation_passed(self) -> bool:
        return self.failure_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_count": self.failure_count,
            "frames": [frame.to_dict() for frame in self.frames],
            "inventory_path": self.inventory_path,
            "mapped_source_count": self.mapped_source_count,
            "output_directory": self.output_directory,
            "run_id": self.run_id,
            "schema_name": self.schema_name,
            "schema_path": self.schema_path,
            "schema_sha256": self.schema_sha256,
            "schema_validation_passed": self.schema_validation_passed,
            "schema_version": self.schema_version,
            "source_count": self.source_count,
        }
