"""Atomic Zstandard Parquet and JSON serialization for M1.8."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.inventory.hashing import sha256_file
from scene.miniature.exceptions import MiniatureDatasetError
from scene.miniature.models import MiniatureDataset


@dataclass(frozen=True, slots=True)
class MiniatureArtifacts:
    miniature_scene_parquet: Path
    scene_building_candidates_parquet: Path
    scene_road_link_candidates_parquet: Path
    scene_road_node_candidates_parquet: Path
    scene_poi_candidates_parquet: Path
    scene_raster_sources_parquet: Path
    summary_json: Path
    validation_json: Path
    provenance_parquet: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in asdict(self).items()
        }


def _write_parquet(frame: object, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    try:
        pq.write_table(table, temporary, compression="zstd", version="2.6")
        temporary.replace(path)
    except (OSError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise MiniatureDatasetError(
            f"cannot write miniature Parquet {path}: {exc}"
        ) from exc


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
        raise MiniatureDatasetError(
            f"cannot write miniature JSON {path}: {exc}"
        ) from exc


def write_miniature_artifacts(
    dataset: MiniatureDataset,
    validation: dict[str, object],
    summary: dict[str, object],
    output_directory: Path,
) -> MiniatureArtifacts:
    """Write only contracted tabular references; never geometry or pixels."""

    output_directory.mkdir(parents=True, exist_ok=False)
    artifacts = MiniatureArtifacts(
        miniature_scene_parquet=output_directory / "miniature_scene.parquet",
        scene_building_candidates_parquet=(
            output_directory / "scene_building_candidates.parquet"
        ),
        scene_road_link_candidates_parquet=(
            output_directory / "scene_road_link_candidates.parquet"
        ),
        scene_road_node_candidates_parquet=(
            output_directory / "scene_road_node_candidates.parquet"
        ),
        scene_poi_candidates_parquet=(
            output_directory / "scene_poi_candidates.parquet"
        ),
        scene_raster_sources_parquet=(
            output_directory / "scene_raster_sources.parquet"
        ),
        summary_json=output_directory / "summary.json",
        validation_json=output_directory / "validation.json",
        provenance_parquet=output_directory / "provenance.parquet",
    )
    frames = {
        artifacts.miniature_scene_parquet: dataset.scenes,
        artifacts.scene_building_candidates_parquet: (
            dataset.building_candidates
        ),
        artifacts.scene_road_link_candidates_parquet: (
            dataset.road_link_candidates
        ),
        artifacts.scene_road_node_candidates_parquet: (
            dataset.road_node_candidates
        ),
        artifacts.scene_poi_candidates_parquet: dataset.poi_candidates,
        artifacts.scene_raster_sources_parquet: dataset.raster_sources,
        artifacts.provenance_parquet: dataset.provenance,
    }
    for path, frame in frames.items():
        if "geometry" in frame.columns:
            raise MiniatureDatasetError(
                f"geometry serialization is forbidden in M1.8: {path.name}"
            )
        _write_parquet(frame, path)
    artifact_hashes = {
        path.name: sha256_file(path)
        for path in frames
    }
    _write_json(
        artifacts.summary_json,
        {**summary, "artifact_sha256": artifact_hashes},
    )
    _write_json(artifacts.validation_json, validation)
    return artifacts
