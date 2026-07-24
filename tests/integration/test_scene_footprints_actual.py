from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pyogrio

from scene.core.config import load_config
from scene.core.run_context import collect_run_metadata
from scene.scenes.generator import generate_scene_footprints
from scene.scenes.serialization import write_scene_artifacts
from scene.scenes.statistics import scene_statistics
from scene.scenes.validator import validate_scene_result
from scene.scenes.validator import (
    _cross_split_scene_intersections,
    _other_split_mapping_count,
)


@lru_cache(maxsize=1)
def _actual(project_config_path: str):
    config = load_config(project_config_path)
    first = generate_scene_footprints(config)
    second = generate_scene_footprints(config)
    validation = validate_scene_result(first, config, regenerated=second)
    return config, first, validation


def test_actual_scene_generation_is_valid(project_config_path: Path) -> None:
    _, result, validation = _actual(str(project_config_path))
    assert len(result.scenes) > 0
    assert set(result.scenes["split"]) == {"train", "validation", "test"}
    assert validation["valid"]
    assert validation["determinism"]["valid"]
    assert len(result.scenes) == 6_916
    assert all(
        row["approved_formula_applied"]
        for row in result.allowable_regions.pair_audit
    )
    assert _cross_split_scene_intersections(result.scenes) == 0
    assert _other_split_mapping_count(
        result.district_mapping,
        result.districts,
    ) == (0, 0)


def test_validator_independently_detects_cross_split_errors(
    project_config_path: Path,
) -> None:
    _, result, _ = _actual(str(project_config_path))
    train = result.scenes.loc[result.scenes["split"] == "train"].iloc[[0]].copy()
    validation = result.scenes.loc[
        result.scenes["split"] == "validation"
    ].iloc[[0]].copy()
    validation.geometry = [train.geometry.iloc[0]]
    overlapping = gpd.GeoDataFrame(
        pd.concat([train, validation], ignore_index=True),
        geometry="geometry",
        crs=result.scenes.crs,
    )
    assert _cross_split_scene_intersections(overlapping) == 1

    mismatched = result.district_mapping.iloc[[0]].copy()
    district_split = result.districts.set_index("district_id").loc[
        mismatched.iloc[0]["district_id"],
        "split",
    ]
    mismatched.loc[:, "split"] = (
        "validation" if district_split != "validation" else "train"
    )
    assert _other_split_mapping_count(
        mismatched,
        result.districts,
    ) == (0, 1)


def test_actual_scene_serialization(
    project_config_path: Path,
    tmp_path: Path,
) -> None:
    config, result, validation = _actual(str(project_config_path))
    statistics = scene_statistics(result, config)
    metadata = collect_run_metadata(config)
    statistics["run_metadata"] = metadata.to_dict()
    artifacts = write_scene_artifacts(
        result,
        validation,
        statistics,
        tmp_path / "scenes",
        metadata,
        canonical_boundary_hash="a" * 64,
    )
    assert len(
        pyogrio.read_dataframe(
            artifacts.scene_footprints_gpkg,
            layer="scene_footprints",
        )
    ) == len(result.scenes)
    parquet = pq.read_table(artifacts.scene_footprints_parquet)
    assert parquet.num_rows == len(result.scenes)
    assert parquet.schema.names == list(result.scenes.drop(columns="geometry"))
    assert {
        pq.ParquetFile(artifacts.scene_footprints_parquet)
        .metadata.row_group(0)
        .column(index)
        .compression
        for index in range(parquet.num_columns)
    } == {"ZSTD"}
    provenance = pq.read_table(artifacts.provenance_parquet)
    assert set(provenance["shapely_version"].to_pylist()) == {
        metadata.shapely_version
    }
    assert set(provenance["geos_version"].to_pylist()) == {
        metadata.geos_version
    }
