"""Atomic JSON serialization for the M1.9 audit bundle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from scene.release_validation.exceptions import ReleaseValidationError


@dataclass(frozen=True, slots=True)
class ReleaseValidationArtifacts:
    pipeline_replay: Path
    hash_comparison: Path
    geometry_audit: Path
    id_audit: Path
    manifest_audit: Path
    provenance_audit: Path
    repository_audit: Path
    performance: Path
    release_summary: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in self.__dataclass_fields__
        }


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise ReleaseValidationError(
            f"cannot serialize release audit {path}: {exc}"
        ) from exc


def write_release_artifacts(
    output_directory: Path,
    payloads: Mapping[str, object],
) -> ReleaseValidationArtifacts:
    output_directory.mkdir(parents=True, exist_ok=True)
    paths = ReleaseValidationArtifacts(
        pipeline_replay=output_directory / "pipeline_replay.json",
        hash_comparison=output_directory / "hash_comparison.json",
        geometry_audit=output_directory / "geometry_audit.json",
        id_audit=output_directory / "id_audit.json",
        manifest_audit=output_directory / "manifest_audit.json",
        provenance_audit=output_directory / "provenance_audit.json",
        repository_audit=output_directory / "repository_audit.json",
        performance=output_directory / "performance.json",
        release_summary=output_directory / "release_summary.json",
    )
    for key in paths.__dataclass_fields__:
        _write_json(getattr(paths, key), payloads[key])
    return paths
