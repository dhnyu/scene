"""Independent M1.8 invariant validation."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from scene.miniature.exceptions import MiniatureDatasetError
from scene.miniature.models import MiniatureDataset


def _mapping_validation(
    frame: pd.DataFrame,
    *,
    id_column: str,
    known_ids: set[str],
    scene_split: dict[str, str],
) -> dict[str, int]:
    null_ids = int(frame[id_column].isna().sum())
    unknown_ids = int(
        (~frame[id_column].isin(known_ids) & frame[id_column].notna()).sum()
    )
    missing_scene = int(
        (~frame["scene_footprint_id"].isin(scene_split)).sum()
    )
    duplicate_mapping = int(
        frame.duplicated(["scene_footprint_id", id_column]).sum()
    )
    joined = frame.loc[
        frame["scene_footprint_id"].isin(scene_split)
        & frame[id_column].notna(),
        ["scene_footprint_id", id_column],
    ].copy()
    joined["split"] = joined["scene_footprint_id"].map(scene_split)
    split_mismatch = int(
        (joined.groupby(id_column)["split"].nunique() > 1).sum()
    )
    candidate_only_false = int((frame["candidate_only"] != True).sum())  # noqa: E712
    return {
        "candidate_only_false_count": candidate_only_false,
        "duplicate_mapping_count": duplicate_mapping,
        "mapping_count": len(frame),
        "null_id_count": null_ids,
        "scene_without_reference_count": missing_scene,
        "split_mismatch_object_count": split_mismatch,
        "unknown_id_count": unknown_ids,
    }


def validate_dataset(
    dataset: MiniatureDataset,
    *,
    split_order: tuple[str, ...],
    scenes_per_split: int,
    known_ids: Mapping[str, set[str]],
    regenerated: MiniatureDataset | None = None,
) -> dict[str, object]:
    """Validate scene, mapping, raster and deterministic content contracts."""

    scene_split = dict(
        zip(
            dataset.scenes["scene_footprint_id"].astype(str),
            dataset.scenes["split"].astype(str),
            strict=True,
        )
    )
    scene_counts = {
        split: int((dataset.scenes["split"].astype(str) == split).sum())
        for split in split_order
    }
    scene_validation = {
        "duplicate_scene_id_count": int(
            dataset.scenes["scene_footprint_id"].duplicated().sum()
        ),
        "scene_count": len(dataset.scenes),
        "scene_count_by_split": scene_counts,
        "unexpected_split_count": int(
            (~dataset.scenes["split"].isin(split_order)).sum()
        ),
    }
    mappings: dict[str, dict[str, int]] = {}
    id_columns = {
        "building": "building_id",
        "poi": "poi_id",
        "road_link": "road_link_id",
        "road_node": "road_node_id",
    }
    for entity, frame in dataset.candidate_frames().items():
        mappings[entity] = _mapping_validation(
            frame,
            id_column=id_columns[entity],
            known_ids=known_ids[entity],
            scene_split=scene_split,
        )
    raster = {
        "duplicate_scene_count": int(
            dataset.raster_sources["scene_footprint_id"].duplicated().sum()
        ),
        "missing_dem_source_count": int(
            dataset.raster_sources["dem_source"].isna().sum()
        ),
        "missing_landcover_source_count": int(
            dataset.raster_sources["landcover_source"].isna().sum()
        ),
        "raster_reference_count": len(dataset.raster_sources),
        "scene_without_raster_count": len(
            set(scene_split)
            - set(dataset.raster_sources["scene_footprint_id"].astype(str))
        ),
        "unknown_scene_count": int(
            (~dataset.raster_sources["scene_footprint_id"].isin(scene_split)).sum()
        ),
    }
    provenance_columns = {
        "assignment_hash",
        "canonical_boundary_hash",
        "config_hash",
        "miniature_content_hash",
        "run_id",
        "scene_content_hash",
        "scene_footprint_id",
        "scene_generation_version",
    }
    provenance = {
        "missing_column_count": len(
            provenance_columns - set(dataset.provenance.columns)
        ),
        "null_value_count": int(dataset.provenance.isna().sum().sum()),
        "row_count": len(dataset.provenance),
        "scene_id_set_match": set(
            dataset.provenance["scene_footprint_id"].astype(str)
        )
        == set(scene_split),
    }
    deterministic = {
        "content_hash_match": (
            regenerated is not None
            and regenerated.content_hash == dataset.content_hash
        ),
        "mapping_match": regenerated is not None
        and all(
            dataset.candidate_frames()[key].equals(
                regenerated.candidate_frames()[key]
            )
            for key in dataset.candidate_frames()
        ),
        "raster_match": regenerated is not None
        and dataset.raster_sources.equals(regenerated.raster_sources),
        "scene_match": regenerated is not None
        and dataset.scenes.equals(regenerated.scenes),
    }
    failures: list[str] = []
    if scene_validation["scene_count"] != scenes_per_split * len(split_order):
        failures.append("scene count is not 9")
    if any(count != scenes_per_split for count in scene_counts.values()):
        failures.append("split scene count is not 3")
    if scene_validation["duplicate_scene_id_count"]:
        failures.append("duplicate scene IDs")
    for entity, values in mappings.items():
        if any(
            values[key]
            for key in (
                "candidate_only_false_count",
                "duplicate_mapping_count",
                "null_id_count",
                "scene_without_reference_count",
                "split_mismatch_object_count",
                "unknown_id_count",
            )
        ):
            failures.append(f"{entity} candidate mapping is invalid")
    if any(
        raster[key]
        for key in (
            "duplicate_scene_count",
            "missing_dem_source_count",
            "missing_landcover_source_count",
            "scene_without_raster_count",
            "unknown_scene_count",
        )
    ):
        failures.append("raster source mapping is invalid")
    if (
        provenance["missing_column_count"]
        or provenance["null_value_count"]
        or not provenance["scene_id_set_match"]
    ):
        failures.append("provenance is incomplete")
    if not all(deterministic.values()):
        failures.append("deterministic regeneration failed")
    validation: dict[str, object] = {
        "determinism": deterministic,
        "failure_count": len(failures),
        "failures": failures,
        "mappings": mappings,
        "passed": not failures,
        "provenance": provenance,
        "raster": raster,
        "scenes": scene_validation,
    }
    if failures:
        raise MiniatureDatasetError("; ".join(failures))
    return validation
