from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
import torch
from shapely.geometry import LineString, Polygon

from scene.m4.geometry_encoder import (
    GEOMETRY_ENCODER_SEED,
    encode_observation_geometry,
    fourier_to_magnitude_phase,
    initialize_geometry_encoder,
    intrinsic_geometry,
    state_dict_sha256,
)
from scene.m4.geometry_materialization import GeometryTask, _encode_task, _scan_global_ids
from scene.m4.geometry_materialization import (
    SHARD_SCHEMA_VERSION,
    architecture_hash,
    frequency_config_hash,
    frequency_tensor_hash,
    task_plan_hash,
    validate_shard_provenance,
)
from scene.m4.workflow import run_m4_stage


def _evidence(path: Path, stage_id: str) -> Path:
    path.mkdir(parents=True)
    prefix = stage_id.lower().replace(".", "_")
    marker = stage_id.replace(".", "_")
    for filename in (
        f"M{marker[1:]}_PASS",
        f"{prefix}_stage_manifest.json",
        f"{prefix}_acceptance_result.json",
        f"{prefix}_audit_result.json",
    ):
        (path / filename).write_text("{}\n", encoding="utf-8")
    return path


def test_m4_geometry_feature_contract_and_zero_phase() -> None:
    fourier = torch.tensor([[0.0 + 0.0j, 3.0 + 4.0j] + [1.0 + 0.0j] * 126], dtype=torch.complex64)
    features = fourier_to_magnitude_phase(fourier)

    assert features.fourier_magnitude.shape == (1, 128)
    assert features.fourier_phase.shape == (1, 128)
    assert features.x_mag.shape == (1, 128)
    assert features.x_phase.shape == (1, 256)
    assert features.fourier_phase[0, 0].item() == pytest.approx(0.0)
    assert features.x_phase[0, 0].item() == pytest.approx(1.0)
    assert features.x_phase[0, 128].item() == pytest.approx(0.0)
    assert torch.isfinite(features.x_mag).all()
    assert torch.isfinite(features.x_phase).all()


def test_geometry_encoder_architecture_state_and_eval_determinism() -> None:
    first = initialize_geometry_encoder(seed=GEOMETRY_ENCODER_SEED)
    second = initialize_geometry_encoder(seed=GEOMETRY_ENCODER_SEED)
    assert state_dict_sha256(first) == state_dict_sha256(second)

    x_mag = torch.rand((3, 128), dtype=torch.float32)
    x_phase = torch.rand((3, 256), dtype=torch.float32)
    with torch.no_grad():
        out_a = first(x_mag, x_phase)
        out_b = first(x_mag, x_phase)
    assert out_a.e_mag.shape == (3, 128)
    assert out_a.e_phase.shape == (3, 128)
    assert out_a.e_geom.shape == (3, 128)
    assert torch.equal(out_a.e_geom, out_b.e_geom)
    assert torch.isfinite(out_a.e_geom).all()


def test_intrinsic_geometry_translation_invariance() -> None:
    polygon = Polygon([(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)])
    shifted = Polygon([(110.0, -40.0), (120.0, -40.0), (120.0, -30.0), (110.0, -30.0)])
    a = intrinsic_geometry(polygon, representative_x=15.0, representative_y=15.0)
    b = intrinsic_geometry(shifted, representative_x=115.0, representative_y=-35.0)
    assert a.equals_exact(b, tolerance=1.0e-12)

    fa = encode_observation_geometry(polygon, object_type="building", representative_x=15.0, representative_y=15.0)
    fb = encode_observation_geometry(shifted, object_type="building", representative_x=115.0, representative_y=-35.0)
    assert torch.allclose(fa, fb, atol=1.0e-5, rtol=1.0e-5)


def test_m4_4_to_m4_7_stage_gates_do_not_materialize(tmp_path: Path) -> None:
    m4_3a = _evidence(tmp_path / "M4.3A", "M4.3A")
    result_44 = run_m4_stage(Path("configs/m4/m4_skeleton.yaml"), stage_id="M4.4", output_dir=tmp_path / "out", m4_3a_dir=m4_3a)
    m4_4 = Path(str(result_44["stage_dir"]))
    result_45 = run_m4_stage(Path("configs/m4/m4_skeleton.yaml"), stage_id="M4.5", output_dir=tmp_path / "out", m4_4_dir=m4_4)
    m4_5 = Path(str(result_45["stage_dir"]))
    result_46 = run_m4_stage(Path("configs/m4/m4_skeleton.yaml"), stage_id="M4.6", output_dir=tmp_path / "out", m4_5_dir=m4_5)
    m4_6 = Path(str(result_46["stage_dir"]))
    result_47 = run_m4_stage(Path("configs/m4/m4_skeleton.yaml"), stage_id="M4.7", output_dir=tmp_path / "out", m4_6_dir=m4_6)
    m4_7 = Path(str(result_47["stage_dir"]))

    assert result_44["status"] == "PASS"
    assert result_45["status"] == "PASS"
    assert result_46["status"] == "PASS"
    assert result_47["status"] == "PASS"
    manifest_44 = json.loads((m4_4 / "m4_4_stage_manifest.json").read_text())
    manifest_45 = json.loads((m4_5 / "m4_5_stage_manifest.json").read_text())
    manifest_47 = json.loads((m4_7 / "m4_7_stage_manifest.json").read_text())
    assert manifest_44["previous_stage_id"] == "M4.3A"
    assert len(manifest_44["previous_stage_manifest_sha256"]) == 64
    assert len(manifest_44["previous_stage_pass_marker_sha256"]) == 64
    assert manifest_45["previous_stage_id"] == "M4.4"
    assert len(manifest_45["previous_stage_manifest_sha256"]) == 64
    assert manifest_47["next_stage"] == "M4.8"
    assert not list((tmp_path / "out").glob("**/*.parquet"))
    assert not list((tmp_path / "out").glob("**/*.pt"))


def test_m4_8_worker_materialization_fixture(tmp_path: Path) -> None:
    building_path = tmp_path / "building.gpkg"
    road_path = tmp_path / "road.gpkg"
    building = gpd.GeoDataFrame(
        {
            "release_id": ["r"],
            "split": ["train"],
            "district_id": ["d"],
            "processing_block_id": ["b"],
            "scene_id": ["s"],
            "object_type": ["building"],
            "object_id": ["ob"],
            "part_id": [None],
            "observation_id": ["obs_b"],
            "source_geometry_id": ["sgb"],
            "representative_x": [0.5],
            "representative_y": [0.5],
        },
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:5186",
    )
    road = gpd.GeoDataFrame(
        {
            "release_id": ["r"],
            "split": ["train"],
            "district_id": ["d"],
            "processing_block_id": ["b"],
            "scene_id": ["s"],
            "object_type": ["road"],
            "object_id": ["or"],
            "part_id": [None],
            "observation_id": ["obs_r"],
            "source_geometry_id": ["sgr"],
            "representative_x": [0.5],
            "representative_y": [0.0],
        },
        geometry=[LineString([(0, 0), (1, 0)])],
        crs="EPSG:5186",
    )
    building.to_file(building_path, layer="building_observation", driver="GPKG")
    road.to_file(road_path, layer="road_observation", driver="GPKG")

    state = {key: value.detach().cpu() for key, value in initialize_geometry_encoder().state_dict().items()}
    stage_dir = tmp_path / "stage"
    tasks = [
        GeometryTask("building_task_0001", "geometry_shard_building_0001", "building", str(building_path), "building_observation", 0, 1),
        GeometryTask("road_task_0001", "geometry_shard_road_0001", "road", str(road_path), "road_observation", 0, 1),
    ]
    provenance = {
        "state_dict_hash": "a" * 64,
        "architecture_hash": architecture_hash(),
        "upstream_inventory_hash": "b" * 64,
        "upstream_artifact_hashes": {"building": "c" * 64, "road": "d" * 64},
        "frequency_tensor_hash": frequency_tensor_hash(),
        "frequency_config_hash": frequency_config_hash(),
        "task_plan_hash": task_plan_hash(tasks),
    }
    results = [_encode_task(task, str(stage_dir), state, provenance=provenance) for task in tasks]
    assert all(result["status"] == "PASS" for result in results)
    scan = _scan_global_ids(results, stage_dir)
    assert scan["unique_observation_ids"] == 2
    assert scan["duplicate_observation_id_count"] == 0
    assert scan["embedding_nonfinite_count"] == 0
    assert scan["geometry_frequency_mask_false_count"] == 0
    assert scan["geometry_frequency_mask_null_count"] == 0
    table = pq.read_table(stage_dir / results[0]["file"])
    mask_field = table.schema.field("geometry_frequency_mask")
    assert mask_field.type.list_size == 128
    assert str(mask_field.type.value_type) == "bool"
    mask = table["geometry_frequency_mask"].combine_chunks().values.to_pylist()
    assert mask == [True] * 128
    for result in results:
        assert result["schema_version"] == SHARD_SCHEMA_VERSION
        assert result["state_dict_hash"] == "a" * 64
        assert result["architecture_hash"] == provenance["architecture_hash"]
        assert result["frequency_tensor_hash"] == provenance["frequency_tensor_hash"]
        assert result["task_plan_hash"] == provenance["task_plan_hash"]
        assert result["worker_status"] == "PASS"

    coverage = validate_shard_provenance(
        results,
        expected_state_hash="a" * 64,
        expected_architecture_hash=provenance["architecture_hash"],
        expected_upstream_inventory_hash="b" * 64,
        expected_frequency_tensor_hash=provenance["frequency_tensor_hash"],
        expected_frequency_config_hash=provenance["frequency_config_hash"],
        expected_task_plan_hash=provenance["task_plan_hash"],
    )
    assert coverage["valid"] is True


def test_missing_shard_provenance_field_fails_validation() -> None:
    shard = {
        "state_dict_hash": "a" * 64,
        "architecture_hash": "b" * 64,
        "upstream_artifact_hash": "c" * 64,
        "upstream_inventory_hash": "d" * 64,
        "frequency_tensor_hash": "e" * 64,
        "frequency_config_hash": "f" * 64,
        "task_plan_hash": "1" * 64,
        "schema_version": SHARD_SCHEMA_VERSION,
        "worker_status": "PASS",
    }
    incomplete = dict(shard)
    incomplete.pop("state_dict_hash")
    result = validate_shard_provenance(
        [incomplete],
        expected_state_hash=shard["state_dict_hash"],
        expected_architecture_hash=shard["architecture_hash"],
        expected_upstream_inventory_hash=shard["upstream_inventory_hash"],
        expected_frequency_tensor_hash=shard["frequency_tensor_hash"],
        expected_frequency_config_hash=shard["frequency_config_hash"],
        expected_task_plan_hash=shard["task_plan_hash"],
    )
    assert result["valid"] is False
    assert result["missing_field_count"] > 0
