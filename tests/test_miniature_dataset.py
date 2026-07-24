from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import LineString, Point, box

from scene.cli import build_parser
from scene.core.config import load_config
from scene.miniature.candidate_query import (
    link_raster_metadata,
    load_stable_id_lookup,
    query_candidates,
)
from scene.miniature.exceptions import MiniatureDatasetError
from scene.miniature.mapping import content_hash, provenance_frame
from scene.miniature.models import MiniatureDataset
from scene.miniature.reporting import write_miniature_report
from scene.miniature.selector import select_scenes
from scene.miniature.serialization import write_miniature_artifacts
from scene.miniature.validator import validate_dataset
from scene.miniature.workflow import (
    _assemble,
    _candidate_sources,
    _load_scene_inputs,
)
from scene.core.run_context import RunMetadata


def _scenes() -> gpd.GeoDataFrame:
    rows = []
    for split_index, split in enumerate(("train", "validation", "test")):
        for index in range(4):
            x = float(split_index * 10_000 + index * 500)
            rows.append(
                {
                    "assignment_hash": "a" * 64,
                    "assignment_version": "seoul-district-v1",
                    "grid_col": index,
                    "grid_row": 3 - index,
                    "scene_footprint_id": f"{split}-{index}",
                    "scene_generation_version": "scene-footprint-v1",
                    "split": split,
                    "geometry": box(x, 0.0, x + 500.0, 500.0),
                }
            )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:5186")


def _dataset(run_id: str = "20260724_120000_KST") -> MiniatureDataset:
    selected = select_scenes(
        _scenes(),
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
    )
    scenes = pd.DataFrame(selected.drop(columns="geometry"))
    frames = {
        "building": pd.DataFrame(
            {
                "scene_footprint_id": [scenes.iloc[0]["scene_footprint_id"]],
                "building_id": ["b1"],
                "candidate_only": [True],
            }
        ),
        "road_link": pd.DataFrame(
            {
                "scene_footprint_id": [scenes.iloc[0]["scene_footprint_id"]],
                "road_link_id": ["l1"],
                "candidate_only": [True],
            }
        ),
        "road_node": pd.DataFrame(
            {
                "scene_footprint_id": [scenes.iloc[0]["scene_footprint_id"]],
                "road_node_id": ["n1"],
                "candidate_only": [True],
            }
        ),
        "poi": pd.DataFrame(
            {
                "scene_footprint_id": [scenes.iloc[0]["scene_footprint_id"]],
                "poi_id": ["p1"],
                "candidate_only": [True],
            }
        ),
    }
    raster = pd.DataFrame(
        {
            "scene_footprint_id": scenes["scene_footprint_id"],
            "landcover_source": "seoul_landcover",
            "dem_source": "seoul_dem",
        }
    )
    digest = content_hash(scenes, frames, raster)
    provenance = provenance_frame(
        scene_ids=scenes["scene_footprint_id"],
        assignment_hash="a" * 64,
        scene_content_hash="s" * 64,
        canonical_boundary_hash="b" * 64,
        scene_generation_version="scene-footprint-v1",
        run_id=run_id,
        config_hash="c" * 64,
        miniature_content_hash=digest,
    )
    return MiniatureDataset(
        selected_scene_geometry=selected,
        scenes=scenes,
        building_candidates=frames["building"],
        road_link_candidates=frames["road_link"],
        road_node_candidates=frames["road_node"],
        poi_candidates=frames["poi"],
        raster_sources=raster,
        provenance=provenance,
        content_hash=digest,
    )


def test_scene_selection_is_exact_and_deterministic() -> None:
    source = _scenes().sample(frac=1.0, random_state=9)
    selected = select_scenes(
        source,
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
    )
    assert selected.groupby("split").size().to_dict() == {
        "test": 3,
        "train": 3,
        "validation": 3,
    }
    assert selected.loc[selected["split"] == "train", "grid_col"].tolist() == [
        0,
        1,
        2,
    ]
    repeated = select_scenes(
        source.iloc[::-1],
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
    )
    assert selected["scene_footprint_id"].tolist() == repeated[
        "scene_footprint_id"
    ].tolist()


def test_candidate_query_intersects_and_emits_no_geometry() -> None:
    scenes = _scenes().iloc[:1]
    sources = gpd.GeoDataFrame(
        {
            "native_id": ["inside", "boundary", "outside"],
            "geometry": [
                Point(10.0, 10.0),
                Point(0.0, 250.0),
                Point(-1.0, 250.0),
            ],
        },
        crs="EPSG:5186",
    )
    result = query_candidates(
        sources,
        scenes,
        source_native_id_field="native_id",
        output_id_field="poi_id",
        stable_ids={"inside": "p1", "boundary": "p2", "outside": "p3"},
    )
    assert set(result["poi_id"]) == {"p1", "p2"}
    assert list(result.columns) == [
        "scene_footprint_id",
        "poi_id",
        "candidate_only",
    ]


def test_candidate_query_accepts_line_intersection() -> None:
    scenes = _scenes().iloc[:1]
    sources = gpd.GeoDataFrame(
        {
            "native_id": ["crossing"],
            "geometry": [LineString([(-10.0, 250.0), (510.0, 250.0)])],
        },
        crs="EPSG:5186",
    )
    result = query_candidates(
        sources,
        scenes,
        source_native_id_field="native_id",
        output_id_field="road_link_id",
        stable_ids={"crossing": "l1"},
    )
    assert result["road_link_id"].tolist() == ["l1"]


def test_stable_id_lookup(tmp_path: Path) -> None:
    path = tmp_path / "ids.parquet"
    table = pa.table(
        {
            "entity_type": ["poi", "building"],
            "source_native_id": ["native", "building"],
            "canonical_object_id": ["stable", "stable-building"],
        }
    )
    pq.write_table(table, path)
    assert load_stable_id_lookup(path, "poi") == {"native": "stable"}


def test_raster_metadata_reference_only(tmp_path: Path) -> None:
    path = tmp_path / "raster.parquet"
    frame = pd.DataFrame(
        {
            "source_name": ["land", "dem"],
            "extent_min_x": [-1.0, -1.0],
            "extent_min_y": [-1.0, -1.0],
            "extent_max_x": [40_000.0, 40_000.0],
            "extent_max_y": [1_000.0, 1_000.0],
            "source_reference_only": [True, True],
            "pixel_data_read": [False, False],
            "pixel_data_copied": [False, False],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame), path)
    result = link_raster_metadata(
        _scenes(),
        path,
        landcover_source="land",
        dem_source="dem",
    )
    assert len(result) == 12
    assert "geometry" not in result


def test_raster_metadata_rejects_pixel_read(tmp_path: Path) -> None:
    path = tmp_path / "raster.parquet"
    frame = pd.DataFrame(
        {
            "source_name": ["land", "dem"],
            "extent_min_x": [-1.0, -1.0],
            "extent_min_y": [-1.0, -1.0],
            "extent_max_x": [40_000.0, 40_000.0],
            "extent_max_y": [1_000.0, 1_000.0],
            "source_reference_only": [True, True],
            "pixel_data_read": [True, False],
            "pixel_data_copied": [False, False],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame), path)
    with pytest.raises(MiniatureDatasetError, match="reference-only"):
        link_raster_metadata(
            _scenes(),
            path,
            landcover_source="land",
            dem_source="dem",
        )


def test_validation_and_determinism() -> None:
    dataset = _dataset()
    regenerated = _dataset()
    validation = validate_dataset(
        dataset,
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
        known_ids={
            "building": {"b1"},
            "road_link": {"l1"},
            "road_node": {"n1"},
            "poi": {"p1"},
        },
        regenerated=regenerated,
    )
    assert validation["passed"] is True
    assert validation["failure_count"] == 0


def test_validator_rejects_cross_split_object() -> None:
    dataset = _dataset()
    extra = dataset.building_candidates.iloc[[0]].copy()
    extra["scene_footprint_id"] = dataset.scenes.loc[
        dataset.scenes["split"] == "test", "scene_footprint_id"
    ].iloc[0]
    changed = MiniatureDataset(
        selected_scene_geometry=dataset.selected_scene_geometry,
        scenes=dataset.scenes,
        building_candidates=pd.concat(
            [dataset.building_candidates, extra],
            ignore_index=True,
        ),
        road_link_candidates=dataset.road_link_candidates,
        road_node_candidates=dataset.road_node_candidates,
        poi_candidates=dataset.poi_candidates,
        raster_sources=dataset.raster_sources,
        provenance=dataset.provenance,
        content_hash=dataset.content_hash,
    )
    with pytest.raises(MiniatureDatasetError, match="building"):
        validate_dataset(
            changed,
            split_order=("train", "validation", "test"),
            scenes_per_split=3,
            known_ids={
                "building": {"b1"},
                "road_link": {"l1"},
                "road_node": {"n1"},
                "poi": {"p1"},
            },
            regenerated=changed,
        )


def test_serialization_is_zstd_and_geometry_free(tmp_path: Path) -> None:
    dataset = _dataset()
    validation = validate_dataset(
        dataset,
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
        known_ids={
            "building": {"b1"},
            "road_link": {"l1"},
            "road_node": {"n1"},
            "poi": {"p1"},
        },
        regenerated=_dataset(),
    )
    artifacts = write_miniature_artifacts(
        dataset,
        validation,
        {"status": "complete"},
        tmp_path / "run",
    )
    for path in (
        artifacts.miniature_scene_parquet,
        artifacts.scene_building_candidates_parquet,
        artifacts.scene_road_link_candidates_parquet,
        artifacts.scene_road_node_candidates_parquet,
        artifacts.scene_poi_candidates_parquet,
        artifacts.scene_raster_sources_parquet,
        artifacts.provenance_parquet,
    ):
        parquet = pq.ParquetFile(path)
        assert "geometry" not in parquet.schema_arrow.names
        assert {
            parquet.metadata.row_group(group)
            .column(column)
            .compression
            for group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.row_group(group).num_columns)
        } == {"ZSTD"}
    assert json.loads(artifacts.validation_json.read_text())["passed"] is True


def test_report_contains_provenance_and_read_only(tmp_path: Path) -> None:
    dataset = _dataset()
    validation = validate_dataset(
        dataset,
        split_order=("train", "validation", "test"),
        scenes_per_split=3,
        known_ids={
            "building": {"b1"},
            "road_link": {"l1"},
            "road_node": {"n1"},
            "poi": {"p1"},
        },
        regenerated=_dataset(),
    )
    artifacts = write_miniature_artifacts(
        dataset,
        validation,
        {"status": "complete"},
        tmp_path / "output",
    )
    metadata = RunMetadata(
        run_id="20260724_120000_KST",
        started_at_kst="2026-07-24T12:00:00+09:00",
        git_commit="unavailable",
        python_version="3.14.0",
        platform="test",
        shapely_version="2.1.1",
        geos_version="3.13.1",
        resolved_config_hash="c" * 64,
    )
    reports = write_miniature_report(
        dataset,
        validation,
        artifacts,
        tmp_path / "reports",
        metadata,
        provenance={"assignment_hash": "a" * 64},
        read_only={"changed_input_count": 0, "unchanged": True},
        verification={"pytest": "PASS"},
    )
    payload = json.loads(reports.json.read_text())
    assert payload["summary"]["read_only_verification"]["unchanged"] is True
    assert payload["summary"]["provenance"]["assignment_hash"] == "a" * 64
    assert len(payload["summary"]["selected_scenes"]) == 9


def test_cli_miniature_help(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["miniature", "create", "--help"])
    assert exit_info.value.code == 0
    assert "--config" in capsys.readouterr().out


def test_actual_miniature_candidate_integration(
    project_config_path: Path,
) -> None:
    config = load_config(project_config_path)
    assert config.miniature_dataset is not None
    selected, scene_summary, assignment_lock = _load_scene_inputs(config)
    stable_ids = {
        entity: load_stable_id_lookup(
            config.miniature_dataset.stable_ids_path,
            entity,
        )
        for entity in ("building", "road_link", "road_node", "poi")
    }
    from scene.core.run_context import collect_run_metadata
    from scene.inventory.hashing import sha256_file

    dataset = _assemble(
        config,
        collect_run_metadata(config),
        selected,
        stable_ids,
        scene_content_hash=str(scene_summary["scene_content_hash"]),
        assignment_lock=assignment_lock,
        canonical_boundary_hash=sha256_file(
            config.district_assignment.canonical_boundary_path
        ),
    )
    assert len(dataset.scenes) == 9
    assert dataset.scenes.groupby("split").size().to_dict() == {
        "test": 3,
        "train": 3,
        "validation": 3,
    }
    assert len(dataset.raster_sources) == 9
    assert all(
        "geometry" not in frame.columns
        for frame in (
            dataset.scenes,
            *dataset.candidate_frames().values(),
            dataset.raster_sources,
        )
    )
    assert all(
        source.source_path.is_file()
        for source in _candidate_sources(config)
    )
