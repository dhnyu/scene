"""Typed project configuration loading and canonical serialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from scene.core.exceptions import ConfigurationError


_ROOT_KEYS = {"schema_version", "project_name", "timezone", "paths", "storage"}
_PATH_KEYS = {
    "project_root",
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


@dataclass(frozen=True, slots=True)
class PathConfig:
    """Resolved path roles for project inputs and outputs."""

    project_root: Path
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
class ProjectConfig:
    """Validated, fully resolved project configuration."""

    schema_version: str
    project_name: str
    timezone: str
    paths: PathConfig
    storage: StorageConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": self.paths.to_dict(),
            "project_name": self.project_name,
            "schema_version": self.schema_version,
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


def _resolve_path(raw: object, base_dir: Path, context: str) -> Path:
    value = _string(raw, context)
    if "\x00" in value:
        raise ConfigurationError(f"{context} contains a null byte")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


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

    return ProjectConfig(
        schema_version=schema_version,
        project_name=project_name,
        timezone=timezone,
        paths=paths,
        storage=StorageConfig(**normalized_storage),
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
