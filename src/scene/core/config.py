"""Typed project configuration loading and canonical serialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from scene.core.exceptions import ConfigurationError


_ROOT_KEYS = {
    "schema_version",
    "project_name",
    "timezone",
    "paths",
    "storage",
    "sources",
}
_PATH_KEYS = {
    "project_root",
    "canonical_schema",
    "input_root",
    "external_root",
    "output_root",
    "reports_dir",
    "logs_dir",
    "metadata_dir",
    "resolved_config_dir",
    "tmp_dir",
}
_STORAGE_KEYS = {
    "geometry_format",
    "tabular_format",
    "parquet_compression",
    "resolved_config_format",
    "run_summary_format",
    "miniature_raster_format",
    "source_raster_policy",
    "geopackage_usage",
    "per_scene_pt_files",
    "training_cache_format",
}
_STORAGE_VALUES = {
    "geometry_format": "geopackage",
    "tabular_format": "parquet",
    "parquet_compression": "zstd",
    "resolved_config_format": "yaml",
    "run_summary_format": "json",
    "miniature_raster_format": "geotiff",
    "source_raster_policy": "read_only_reference",
    "geopackage_usage": "inspection_and_archive",
    "per_scene_pt_files": "forbidden",
    "training_cache_format": "open",
}
_SOURCE_REQUIRED_KEYS = {"source_name", "category", "kind", "path"}
_SOURCE_OPTIONAL_KEYS = {
    "administrative_level",
    "canonical_adapter",
    "expected_feature_count",
    "expected_geometry_type",
    "geographic_scope",
    "layer",
    "read_only",
    "source_crs",
    "source_format",
}
_SOURCE_KINDS = {"vector", "raster", "tabular"}


@dataclass(frozen=True, slots=True)
class PathConfig:
    """Resolved path roles for project inputs and outputs."""

    project_root: Path
    canonical_schema: Path
    input_root: Path
    external_root: Path
    output_root: Path
    reports_dir: Path
    logs_dir: Path
    metadata_dir: Path
    resolved_config_dir: Path
    tmp_dir: Path

    @property
    def read_only_roots(self) -> tuple[Path, Path]:
        return (self.input_root, self.external_root)

    @property
    def output_directories(self) -> tuple[Path, ...]:
        return (
            self.output_root,
            self.reports_dir,
            self.logs_dir,
            self.metadata_dir,
            self.resolved_config_dir,
            self.tmp_dir,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in sorted(_PATH_KEYS)
        }


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Approved D-011A storage choices and the D-011B open boundary."""

    geometry_format: str
    tabular_format: str
    parquet_compression: str
    resolved_config_format: str
    run_summary_format: str
    miniature_raster_format: str
    source_raster_policy: str
    geopackage_usage: str
    per_scene_pt_files: str
    training_cache_format: str

    def to_dict(self) -> dict[str, str]:
        return {
            key: getattr(self, key)
            for key in sorted(_STORAGE_KEYS)
        }


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One approved read-only source declared by configuration."""

    source_name: str
    category: str
    kind: str
    path: Path
    layer: str | None
    source_format: str | None = None
    source_crs: str | None = None
    administrative_level: str | None = None
    geographic_scope: str | None = None
    expected_geometry_type: str | None = None
    expected_feature_count: int | None = None
    read_only: bool = True
    canonical_adapter: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "administrative_level": self.administrative_level,
            "canonical_adapter": self.canonical_adapter,
            "category": self.category,
            "expected_feature_count": self.expected_feature_count,
            "expected_geometry_type": self.expected_geometry_type,
            "geographic_scope": self.geographic_scope,
            "kind": self.kind,
            "layer": self.layer,
            "path": str(self.path),
            "read_only": self.read_only,
            "source_crs": self.source_crs,
            "source_format": self.source_format,
            "source_name": self.source_name,
        }


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Validated, fully resolved project configuration."""

    schema_version: str
    project_name: str
    timezone: str
    paths: PathConfig
    storage: StorageConfig
    sources: tuple[SourceConfig, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": self.paths.to_dict(),
            "project_name": self.project_name,
            "schema_version": self.schema_version,
            "sources": [
                source.to_dict()
                for source in sorted(
                    self.sources,
                    key=lambda item: item.source_name,
                )
            ],
            "storage": self.storage.to_dict(),
            "timezone": self.timezone,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{context} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{context} keys must be strings")
    return value


def _validate_keys(
    value: Mapping[str, object],
    required: set[str],
    context: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ConfigurationError(
            f"{context} is missing required keys: {', '.join(missing)}"
        )
    if unknown:
        raise ConfigurationError(
            f"{context} contains unknown keys: {', '.join(unknown)}"
        )


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context} must be a non-empty string")
    return value.strip()


def _optional_string(
    value: Mapping[str, object],
    key: str,
    context: str,
) -> str | None:
    raw = value.get(key)
    return _string(raw, f"{context}.{key}") if raw is not None else None


def _resolve_path(raw: object, base_dir: Path, context: str) -> Path:
    value = _string(raw, context)
    if "\x00" in value:
        raise ConfigurationError(f"{context} contains a null byte")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _load_sources(
    raw: object,
    input_root: Path,
) -> tuple[SourceConfig, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError("sources must be a list")

    sources: list[SourceConfig] = []
    names: set[str] = set()
    allowed_keys = _SOURCE_REQUIRED_KEYS | _SOURCE_OPTIONAL_KEYS
    for index, item in enumerate(raw):
        context = f"sources[{index}]"
        value = _mapping(item, context)
        missing = sorted(_SOURCE_REQUIRED_KEYS - set(value))
        unknown = sorted(set(value) - allowed_keys)
        if missing:
            raise ConfigurationError(
                f"{context} is missing required keys: {', '.join(missing)}"
            )
        if unknown:
            raise ConfigurationError(
                f"{context} contains unknown keys: {', '.join(unknown)}"
            )

        source_name = _string(value["source_name"], f"{context}.source_name")
        if re.fullmatch(r"[a-z][a-z0-9_]*", source_name) is None:
            raise ConfigurationError(
                f"{context}.source_name must use lowercase snake_case"
            )
        if source_name in names:
            raise ConfigurationError(f"duplicate source_name: {source_name}")
        names.add(source_name)

        category = _string(value["category"], f"{context}.category").lower()
        kind = _string(value["kind"], f"{context}.kind").lower()
        if kind not in _SOURCE_KINDS:
            raise ConfigurationError(
                f"{context}.kind must be one of: "
                f"{', '.join(sorted(_SOURCE_KINDS))}"
            )

        layer_value = value.get("layer")
        layer = (
            _string(layer_value, f"{context}.layer")
            if layer_value is not None
            else None
        )
        if kind == "vector" and layer is None:
            raise ConfigurationError(f"{context}.layer is required for vector")
        if kind != "vector" and layer is not None:
            raise ConfigurationError(
                f"{context}.layer is only allowed for vector sources"
            )

        read_only_raw = value.get("read_only", True)
        if not isinstance(read_only_raw, bool):
            raise ConfigurationError(f"{context}.read_only must be boolean")
        source_path = _resolve_path(
            value["path"],
            input_root,
            f"{context}.path",
        )
        explicitly_external_read_only = (
            value.get("read_only") is True and source_path.is_absolute()
        )
        if (
            not source_path.is_relative_to(input_root)
            and not explicitly_external_read_only
        ):
            raise ConfigurationError(
                f"{context}.path must be inside paths.input_root unless an "
                "absolute external source is explicitly read_only"
            )
        expected_feature_count = value.get("expected_feature_count")
        if expected_feature_count is not None and (
            not isinstance(expected_feature_count, int)
            or isinstance(expected_feature_count, bool)
            or expected_feature_count < 0
        ):
            raise ConfigurationError(
                f"{context}.expected_feature_count must be a non-negative integer"
            )
        sources.append(
            SourceConfig(
                source_name=source_name,
                category=category,
                kind=kind,
                path=source_path,
                layer=layer,
                source_format=_optional_string(value, "source_format", context),
                source_crs=_optional_string(value, "source_crs", context),
                administrative_level=_optional_string(
                    value, "administrative_level", context
                ),
                geographic_scope=_optional_string(
                    value, "geographic_scope", context
                ),
                expected_geometry_type=_optional_string(
                    value, "expected_geometry_type", context
                ),
                expected_feature_count=expected_feature_count,
                read_only=read_only_raw,
                canonical_adapter=_optional_string(
                    value, "canonical_adapter", context
                ),
            )
        )
    return tuple(sources)


def load_config(config_path: str | Path) -> ProjectConfig:
    """Load and strictly validate YAML without creating files or directories."""

    path = Path(config_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ConfigurationError(f"configuration file does not exist: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read configuration {path}: {exc}") from exc

    root = _mapping(raw, "configuration")
    _validate_keys(root, _ROOT_KEYS, "configuration")

    schema_version = _string(root["schema_version"], "schema_version")
    project_name = _string(root["project_name"], "project_name")
    timezone = _string(root["timezone"], "timezone")
    if timezone != "Asia/Seoul":
        raise ConfigurationError("timezone must be Asia/Seoul")

    path_values = _mapping(root["paths"], "paths")
    _validate_keys(path_values, _PATH_KEYS, "paths")
    base_dir = path.parent
    paths = PathConfig(
        **{
            key: _resolve_path(path_values[key], base_dir, f"paths.{key}")
            for key in _PATH_KEYS
        }
    )

    storage_values = _mapping(root["storage"], "storage")
    _validate_keys(storage_values, _STORAGE_KEYS, "storage")
    normalized_storage = {
        key: _string(storage_values[key], f"storage.{key}").lower()
        for key in _STORAGE_KEYS
    }
    for key, expected in _STORAGE_VALUES.items():
        if normalized_storage[key] != expected:
            raise ConfigurationError(
                f"storage.{key} must be {expected!r}, "
                f"got {normalized_storage[key]!r}"
            )

    sources = _load_sources(root["sources"], paths.input_root)
    return ProjectConfig(
        schema_version=schema_version,
        project_name=project_name,
        timezone=timezone,
        paths=paths,
        storage=StorageConfig(**normalized_storage),
        sources=sources,
    )


def write_resolved_config(config: ProjectConfig, destination: str | Path) -> Path:
    """Write a canonical YAML snapshot with its deterministic SHA-256."""

    path = Path(destination)
    payload = {
        "resolved_config": config.to_dict(),
        "resolved_config_hash": config.canonical_hash,
    }
    serialized = yaml.safe_dump(
        payload,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise ConfigurationError(
            f"cannot write resolved configuration {path}: {exc}"
        ) from exc
    return path
