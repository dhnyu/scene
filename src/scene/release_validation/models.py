"""Artifact references used by the M1.9 release audit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReleaseArtifacts:
    inventory_json: Path
    canonical_manifest: Path
    building_directory: Path
    road_directory: Path
    poi_directory: Path
    raster_directory: Path
    ids_directory: Path
    boundary_directory: Path
    split_directory: Path
    scene_directory: Path
    miniature_directory: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class ReplayResult:
    artifacts: ReleaseArtifacts
    stages: tuple[dict[str, object], ...]
    replay_config: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "artifacts": self.artifacts.to_dict(),
            "replay_config": str(self.replay_config),
            "stages": list(self.stages),
        }
