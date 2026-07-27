"""M4.8 production geometry embedding materialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import torch
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from scene.m4.geometry_encoder import (
    GEOMETRY_ENCODER_SEED,
    GeometryEncoder,
    encode_observation_geometry,
    fourier_to_magnitude_phase,
    geometry_architecture_metadata,
    initialize_geometry_encoder,
    state_dict_sha256,
)
from scene.m4.geometry_frequency import generate_frequency_grid
from scene.m4.geometry_module import GeometryFourierPrimitive

M3_RUN_ID = "20260726_025452_KST"
BUILDING_GPKG = Path(
    "outputs/m3/20260726_025452_KST/stages/M3.2/artifacts/observations/building/building_observations.gpkg"
)
ROAD_GPKG = Path(
    "outputs/m3/20260726_025452_KST/stages/M3.3/artifacts/observations/road/road_observations.gpkg"
)
POI_ATTRIBUTES = Path(
    "outputs/m3/20260726_025452_KST/stages/M3.4/artifacts/observations/poi/poi_attributes.parquet"
)
BUILDING_LAYER = "building_observation"
ROAD_LAYER = "road_observation"
MATERIALIZATION_WORKERS = 40
BUILDING_TASKS = 0
ROAD_TASKS = 0
TARGET_ROWS_PER_TASK = 512
FEATURE_COLUMNS = (
    "fourier_real",
    "fourier_imag",
    "fourier_magnitude",
    "fourier_phase",
    "x_mag",
    "x_phase",
    "e_mag",
    "e_phase",
    "e_geom",
)
GEOMETRY_FREQUENCY_MASK_COLUMN = "geometry_frequency_mask"
SHARD_SCHEMA_VERSION = "scene.m4.geometry_shard_completion.v2"
SHARD_MANIFEST_SCHEMA_VERSION = "scene.m4.geometry_shard_manifest.v2"
REQUIRED_SHARD_PROVENANCE_FIELDS = (
    "state_dict_hash",
    "architecture_hash",
    "upstream_artifact_hash",
    "upstream_inventory_hash",
    "frequency_tensor_hash",
    "frequency_config_hash",
    "task_plan_hash",
    "schema_version",
    "worker_status",
)
METADATA_COLUMNS = (
    "release_id",
    "split",
    "district_id",
    "processing_block_id",
    "scene_id",
    "object_type",
    "object_id",
    "part_id",
    "observation_id",
    "source_geometry_id",
    "representative_x",
    "representative_y",
)


@dataclass(frozen=True, slots=True)
class GeometryTask:
    """One deterministic geometry materialization task."""

    task_id: str
    shard_id: str
    object_type: str
    source_path: str
    layer: str
    start: int
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "shard_id": self.shard_id,
            "object_type": self.object_type,
            "source_path": self.source_path,
            "layer": self.layer,
            "start": self.start,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GeometryTask":
        return cls(
            task_id=str(payload["task_id"]),
            shard_id=str(payload["shard_id"]),
            object_type=str(payload["object_type"]),
            source_path=str(payload["source_path"]),
            layer=str(payload["layer"]),
            start=int(payload["start"]),
            count=int(payload["count"]),
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def architecture_hash(architecture: Mapping[str, Any] | None = None) -> str:
    return _sha256_payload(architecture or geometry_architecture_metadata())


def frequency_tensor_hash() -> str:
    omega = generate_frequency_grid(dtype=torch.float64).cpu().numpy()
    return hashlib.sha256(omega.tobytes(order="C")).hexdigest()


def frequency_config_hash() -> str:
    return _sha256_payload(
        {
            "decision": "D-002",
            "frequency_count": 128,
            "radius_count": 8,
            "angle_count": 16,
            "radius_min": 0.5,
            "radius_max": 50.0,
            "ordering": "radius-major, angle-minor",
            "runtime_sorting": False,
        }
    )


def task_plan_hash(tasks: Iterable["GeometryTask"]) -> str:
    return _sha256_payload([task.to_dict() for task in tasks])


def _ranges(total: int, task_count: int) -> list[tuple[int, int]]:
    if total <= 0:
        return []
    task_count = min(task_count, total)
    base = total // task_count
    extra = total % task_count
    ranges: list[tuple[int, int]] = []
    start = 0
    for idx in range(task_count):
        count = base + (1 if idx < extra else 0)
        ranges.append((start, count))
        start += count
    return ranges


def _task_count(total: int, configured: int) -> int:
    if configured > 0:
        return configured
    return max(1, math.ceil(total / TARGET_ROWS_PER_TASK))


def build_geometry_tasks(
    *,
    building_path: Path = BUILDING_GPKG,
    road_path: Path = ROAD_GPKG,
    sample_limit: int | None = None,
) -> list[GeometryTask]:
    """Build deterministic building/road row-range tasks."""

    building_count = int(pyogrio.read_info(building_path, layer=BUILDING_LAYER)["features"])
    road_count = int(pyogrio.read_info(road_path, layer=ROAD_LAYER)["features"])
    if sample_limit is not None:
        building_count = min(building_count, max(0, int(sample_limit)))
        road_count = min(road_count, max(0, int(math.ceil(sample_limit / 10))))

    tasks: list[GeometryTask] = []
    for idx, (start, count) in enumerate(_ranges(building_count, _task_count(building_count, BUILDING_TASKS)), start=1):
        tasks.append(
            GeometryTask(
                task_id=f"building_task_{idx:04d}",
                shard_id=f"geometry_shard_building_{idx:04d}",
                object_type="building",
                source_path=str(building_path),
                layer=BUILDING_LAYER,
                start=start,
                count=count,
            )
        )
    for idx, (start, count) in enumerate(_ranges(road_count, _task_count(road_count, ROAD_TASKS)), start=1):
        tasks.append(
            GeometryTask(
                task_id=f"road_task_{idx:04d}",
                shard_id=f"geometry_shard_road_{idx:04d}",
                object_type="road",
                source_path=str(road_path),
                layer=ROAD_LAYER,
                start=start,
                count=count,
            )
        )
    return tasks


def upstream_geometry_inventory() -> dict[str, Any]:
    """Return read-only upstream geometry inventory for M4.8 provenance."""

    building_info = pyogrio.read_info(BUILDING_GPKG, layer=BUILDING_LAYER)
    road_info = pyogrio.read_info(ROAD_GPKG, layer=ROAD_LAYER)
    poi_rows = int(pq.read_metadata(POI_ATTRIBUTES).num_rows)
    return {
        "run_id": M3_RUN_ID,
        "building": {
            "path": str(BUILDING_GPKG),
            "layer": BUILDING_LAYER,
            "rows": int(building_info["features"]),
            "crs": str(building_info["crs"]),
            "size_bytes": BUILDING_GPKG.stat().st_size,
            "sha256": sha256_file(BUILDING_GPKG),
        },
        "road": {
            "path": str(ROAD_GPKG),
            "layer": ROAD_LAYER,
            "rows": int(road_info["features"]),
            "crs": str(road_info["crs"]),
            "size_bytes": ROAD_GPKG.stat().st_size,
            "sha256": sha256_file(ROAD_GPKG),
        },
        "poi": {
            "path": str(POI_ATTRIBUTES),
            "rows": poi_rows,
            "geometry_modality_available": False,
            "sha256": sha256_file(POI_ATTRIBUTES),
            "size_bytes": POI_ATTRIBUTES.stat().st_size,
        },
    }


def _fixed_list(values: np.ndarray, width: int) -> pa.FixedSizeListArray:
    values = np.asarray(values, dtype=np.float32)
    return pa.FixedSizeListArray.from_arrays(
        pa.array(values.reshape(-1), type=pa.float32()),
        width,
    )


def _fixed_bool_list(values: np.ndarray, width: int) -> pa.FixedSizeListArray:
    values = np.asarray(values, dtype=np.bool_)
    return pa.FixedSizeListArray.from_arrays(
        pa.array(values.reshape(-1), type=pa.bool_()),
        width,
    )


def _make_table(frame: Any, arrays: dict[str, np.ndarray]) -> pa.Table:
    columns: list[pa.Array] = []
    names: list[str] = []
    for name in METADATA_COLUMNS:
        if name in {"representative_x", "representative_y"}:
            columns.append(pa.array(frame[name].to_numpy(dtype=np.float64), type=pa.float64()))
        else:
            values = [None if value is None or str(value) == "<NA>" else str(value) for value in frame[name].tolist()]
            columns.append(pa.array(values, type=pa.string()))
        names.append(name)
    widths = {
        "fourier_real": 128,
        "fourier_imag": 128,
        "fourier_magnitude": 128,
        "fourier_phase": 128,
        "x_mag": 128,
        "x_phase": 256,
        "e_mag": 128,
        "e_phase": 128,
        "e_geom": 128,
    }
    for name in FEATURE_COLUMNS:
        columns.append(_fixed_list(arrays[name], widths[name]))
        names.append(name)
    columns.append(_fixed_bool_list(arrays[GEOMETRY_FREQUENCY_MASK_COLUMN], 128))
    names.append(GEOMETRY_FREQUENCY_MASK_COLUMN)
    columns.append(pa.array([True] * len(frame), type=pa.bool_()))
    names.append("geometry_available")
    return pa.Table.from_arrays(columns, names=names)


def _load_task_frame(task: GeometryTask) -> Any:
    return pyogrio.read_dataframe(
        task.source_path,
        layer=task.layer,
        columns=list(METADATA_COLUMNS),
        skip_features=task.start,
        max_features=task.count,
        read_geometry=True,
        use_arrow=True,
    )


def _encode_task(
    task: GeometryTask,
    stage_dir: str,
    state_dict: dict[str, torch.Tensor],
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    shard_root = Path(stage_dir) / "artifacts" / "geometry" / "shards"
    marker_root = Path(stage_dir) / "artifacts" / "geometry" / "markers"
    shard_root.mkdir(parents=True, exist_ok=True)
    marker_root.mkdir(parents=True, exist_ok=True)
    final_path = shard_root / f"{task.shard_id}.parquet"
    tmp_path = shard_root / f"{task.shard_id}.parquet.tmp"
    marker_path = marker_root / f"{task.shard_id}.complete.json"

    model = GeometryEncoder()
    model.load_state_dict(state_dict)
    model.eval()
    primitive = GeometryFourierPrimitive(device="cpu")
    try:
        frame = _load_task_frame(task)
        if len(frame) != task.count:
            raise RuntimeError(f"read row count mismatch: expected {task.count}, observed {len(frame)}")
        fouriers: list[np.ndarray] = []
        for row in frame.itertuples(index=False):
            geom = getattr(row, "geometry")
            rx = float(getattr(row, "representative_x"))
            ry = float(getattr(row, "representative_y"))
            value = encode_observation_geometry(
                geom,
                object_type=task.object_type,
                representative_x=rx,
                representative_y=ry,
                primitive=primitive,
            )
            fouriers.append(value.detach().cpu().numpy().astype(np.complex64, copy=False))
        fourier_np = np.stack(fouriers).astype(np.complex64, copy=False)
        fourier_tensor = torch.from_numpy(fourier_np)
        features = fourier_to_magnitude_phase(fourier_tensor)
        with torch.no_grad():
            encoded = model(features.x_mag, features.x_phase)
        arrays = {
            "fourier_real": fourier_np.real.astype(np.float32, copy=False),
            "fourier_imag": fourier_np.imag.astype(np.float32, copy=False),
            "fourier_magnitude": features.fourier_magnitude.numpy().astype(np.float32, copy=False),
            "fourier_phase": features.fourier_phase.numpy().astype(np.float32, copy=False),
            "x_mag": features.x_mag.numpy().astype(np.float32, copy=False),
            "x_phase": features.x_phase.numpy().astype(np.float32, copy=False),
            "e_mag": encoded.e_mag.numpy().astype(np.float32, copy=False),
            "e_phase": encoded.e_phase.numpy().astype(np.float32, copy=False),
            "e_geom": encoded.e_geom.numpy().astype(np.float32, copy=False),
            GEOMETRY_FREQUENCY_MASK_COLUMN: np.ones((len(frame), 128), dtype=np.bool_),
        }
        for name, array in arrays.items():
            if not np.isfinite(array).all():
                raise RuntimeError(f"{name} contains NaN or Inf")
        table = _make_table(frame, arrays)
        pq.write_table(table, tmp_path, compression="zstd")
        os.replace(tmp_path, final_path)
        provenance = dict(provenance or {})
        upstream_artifact_hashes = provenance.get("upstream_artifact_hashes", {})
        upstream_artifact_hash = (
            upstream_artifact_hashes.get(task.object_type)
            if isinstance(upstream_artifact_hashes, Mapping)
            else None
        )
        metadata = {
            "schema_id": SHARD_SCHEMA_VERSION,
            "schema_version": SHARD_SCHEMA_VERSION,
            "task_id": task.task_id,
            "shard_id": task.shard_id,
            "object_type": task.object_type,
            "row_count": int(len(frame)),
            "file": f"artifacts/geometry/shards/{final_path.name}",
            "size_bytes": final_path.stat().st_size,
            "sha256": sha256_file(final_path),
            "state_dict_hash": provenance.get("state_dict_hash"),
            "architecture_hash": provenance.get("architecture_hash"),
            "upstream_artifact_hash": upstream_artifact_hash,
            "upstream_inventory_hash": provenance.get("upstream_inventory_hash"),
            "frequency_tensor_hash": provenance.get("frequency_tensor_hash"),
            "frequency_config_hash": provenance.get("frequency_config_hash"),
            "task_plan_hash": provenance.get("task_plan_hash"),
            "elapsed_seconds": time.perf_counter() - started,
            "pid": os.getpid(),
            "worker_status": "PASS",
            "status": "PASS",
            "completion_marker": f"artifacts/geometry/markers/{marker_path.name}",
        }
        marker_tmp = marker_path.with_suffix(".json.tmp")
        marker_tmp.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(marker_tmp, marker_path)
        return metadata
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        return {
            "task_id": task.task_id,
            "shard_id": task.shard_id,
            "object_type": task.object_type,
            "row_count": task.count,
            "status": "FAIL",
            "failure": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": time.perf_counter() - started,
            "pid": os.getpid(),
        }


def _worker_result_path(stage_dir: Path, task: GeometryTask) -> Path:
    return stage_dir / "artifacts" / "geometry" / "worker_results" / f"{task.task_id}.json"


def _write_worker_result(stage_dir: Path, task: GeometryTask, result: dict[str, Any]) -> None:
    path = _worker_result_path(stage_dir, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _run_worker_from_files(task_json: Path, stage_dir: Path, state_dict_path: Path) -> int:
    task = GeometryTask.from_dict(json.loads(task_json.read_text(encoding="utf-8")))
    state_payload = torch.load(state_dict_path, map_location="cpu", weights_only=False)
    result = _encode_task(
        task,
        str(stage_dir),
        state_payload["state_dict"],
        provenance=state_payload.get("provenance"),
    )
    _write_worker_result(stage_dir, task, result)
    return 0 if result.get("status") == "PASS" else 2


def _launch_worker(task_json: Path, stage_dir: Path, state_dict_path: Path, log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("wb")
    worker_env = os.environ.copy()
    worker_env.update(
        {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "TORCH_NUM_THREADS": "1",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scene.m4.geometry_materialization",
            "worker",
            "--task-json",
            str(task_json),
            "--stage-dir",
            str(stage_dir),
            "--state-dict",
            str(state_dict_path),
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=Path.cwd(),
        env=worker_env,
    )
    process._scene_log_handle = log_handle  # type: ignore[attr-defined]
    return process


def _run_subprocess_tasks(
    *,
    tasks: list[GeometryTask],
    stage_dir: Path,
    state_dict_path: Path,
    workers: int,
) -> list[dict[str, Any]]:
    task_dir = stage_dir / "artifacts" / "geometry" / "tasks"
    log_dir = stage_dir / "logs"
    task_dir.mkdir(parents=True, exist_ok=True)
    pending = list(tasks)
    running: dict[subprocess.Popen[bytes], GeometryTask] = {}
    results: list[dict[str, Any]] = []
    total = len(tasks)

    def start_next() -> None:
        if not pending:
            return
        task = pending.pop(0)
        task_json = task_dir / f"{task.task_id}.json"
        task_json.write_text(json.dumps(task.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        process = _launch_worker(
            task_json=task_json,
            stage_dir=stage_dir,
            state_dict_path=state_dict_path,
            log_path=log_dir / f"{task.task_id}.log",
        )
        running[process] = task
        time.sleep(0.01)

    for _ in range(min(workers, len(pending))):
        start_next()

    while running:
        time.sleep(0.5)
        for process, task in list(running.items()):
            code = process.poll()
            if code is None:
                continue
            log_handle = getattr(process, "_scene_log_handle", None)
            if log_handle is not None:
                log_handle.close()
            running.pop(process)
            result_path = _worker_result_path(stage_dir, task)
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                result = {
                    "task_id": task.task_id,
                    "shard_id": task.shard_id,
                    "object_type": task.object_type,
                    "row_count": task.count,
                    "status": "FAIL",
                    "failure": f"worker exited {code} without result file",
                }
            result["exit_code"] = code
            if result.get("status") != "PASS":
                _write_worker_result(stage_dir, task, result)
            results.append(result)
            print(
                json.dumps(
                    {
                        "stage": "M4.8",
                        "completed_tasks": len(results),
                        "total_tasks": total,
                        "running_tasks": len(running),
                        "shard_id": result.get("shard_id"),
                        "status": result.get("status"),
                        "exit_code": result.get("exit_code"),
                        "elapsed_seconds": round(float(result.get("elapsed_seconds", 0.0)), 3),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if result.get("status") != "PASS":
                pending.clear()
                for active_process, active_task in list(running.items()):
                    active_process.terminate()
                    try:
                        active_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        active_process.kill()
                        active_process.wait(timeout=5)
                    active_log = getattr(active_process, "_scene_log_handle", None)
                    if active_log is not None:
                        active_log.close()
                    _write_worker_result(
                        stage_dir,
                        active_task,
                        {
                            "task_id": active_task.task_id,
                            "shard_id": active_task.shard_id,
                            "object_type": active_task.object_type,
                            "row_count": active_task.count,
                            "status": "FAIL",
                            "failure": "terminated after sibling worker failure",
                        },
                    )
                running.clear()
                break
            start_next()
    return results


def _scan_global_ids(shards: Iterable[dict[str, Any]], stage_dir: Path) -> dict[str, Any]:
    seen: set[str] = set()
    duplicate_count = 0
    object_type_counts = {"building": 0, "road": 0}
    nan_inf_count = 0
    mask_false_count = 0
    mask_null_count = 0
    for shard in shards:
        table = pq.read_table(
            stage_dir / shard["file"],
            columns=["observation_id", "object_type", "e_geom", GEOMETRY_FREQUENCY_MASK_COLUMN],
        )
        ids = table["observation_id"].to_pylist()
        for observation_id in ids:
            if observation_id in seen:
                duplicate_count += 1
            else:
                seen.add(observation_id)
        for object_type in table["object_type"].to_pylist():
            if object_type in object_type_counts:
                object_type_counts[object_type] += 1
        e_geom = np.asarray(table["e_geom"].combine_chunks().values.to_numpy()).reshape(-1, 128)
        nan_inf_count += int((~np.isfinite(e_geom)).sum())
        mask_column = table[GEOMETRY_FREQUENCY_MASK_COLUMN].combine_chunks()
        mask_null_count += int(mask_column.null_count)
        mask = np.asarray(mask_column.values.to_numpy(zero_copy_only=False)).reshape(-1, 128)
        mask_false_count += int((~mask).sum())
    return {
        "unique_observation_ids": len(seen),
        "duplicate_observation_id_count": duplicate_count,
        "object_type_counts": object_type_counts,
        "embedding_nonfinite_count": nan_inf_count,
        "geometry_frequency_mask_false_count": mask_false_count,
        "geometry_frequency_mask_null_count": mask_null_count,
    }


def validate_shard_provenance(
    shards: Iterable[dict[str, Any]],
    *,
    expected_state_hash: str,
    expected_architecture_hash: str,
    expected_upstream_inventory_hash: str,
    expected_frequency_tensor_hash: str,
    expected_frequency_config_hash: str,
    expected_task_plan_hash: str,
) -> dict[str, Any]:
    missing_field_count = 0
    mismatch_count = 0
    total = 0
    for shard in shards:
        total += 1
        for field in REQUIRED_SHARD_PROVENANCE_FIELDS:
            if field not in shard or shard[field] in (None, ""):
                missing_field_count += 1
        expected = {
            "state_dict_hash": expected_state_hash,
            "architecture_hash": expected_architecture_hash,
            "upstream_inventory_hash": expected_upstream_inventory_hash,
            "frequency_tensor_hash": expected_frequency_tensor_hash,
            "frequency_config_hash": expected_frequency_config_hash,
            "task_plan_hash": expected_task_plan_hash,
            "schema_version": SHARD_SCHEMA_VERSION,
            "worker_status": "PASS",
        }
        for field, value in expected.items():
            if shard.get(field) != value:
                mismatch_count += 1
        if len(str(shard.get("upstream_artifact_hash", ""))) != 64:
            mismatch_count += 1
    return {
        "shard_count": total,
        "required_fields": list(REQUIRED_SHARD_PROVENANCE_FIELDS),
        "missing_field_count": missing_field_count,
        "mismatch_count": mismatch_count,
        "valid": missing_field_count == 0 and mismatch_count == 0,
    }


def run_geometry_materialization(
    *,
    stage_dir: Path,
    workers: int = MATERIALIZATION_WORKERS,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Materialize initialized-untrained geometry embeddings for building/road."""

    if workers != MATERIALIZATION_WORKERS:
        raise ValueError("M4.8 official materialization requires workers=40")
    stage_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model = initialize_geometry_encoder(seed=GEOMETRY_ENCODER_SEED, device="cpu")
    state_hash = state_dict_sha256(model)
    tasks = build_geometry_tasks(sample_limit=sample_limit)
    plan_hash = task_plan_hash(tasks)
    architecture = geometry_architecture_metadata()
    arch_hash = architecture_hash(architecture)
    freq_hash = frequency_tensor_hash()
    freq_config_hash = frequency_config_hash()
    upstream_inventory = upstream_geometry_inventory() if sample_limit is None else {}
    upstream_hash = _sha256_payload(upstream_inventory) if upstream_inventory else "0" * 64
    upstream_artifact_hashes = {
        "building": upstream_inventory.get("building", {}).get("sha256") if upstream_inventory else "0" * 64,
        "road": upstream_inventory.get("road", {}).get("sha256") if upstream_inventory else "0" * 64,
    }
    state_dict = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    state_dict_path = stage_dir / "artifacts" / "geometry" / "geometry_encoder_worker_state.tmp.pt"
    state_dict_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "schema_id": "scene.m4.geometry_worker_state.v1",
            "model_status": "initialized_untrained",
            "state_dict_hash": state_hash,
            "provenance": {
                "state_dict_hash": state_hash,
                "architecture_hash": arch_hash,
                "upstream_inventory_hash": upstream_hash,
                "upstream_artifact_hashes": upstream_artifact_hashes,
                "frequency_tensor_hash": freq_hash,
                "frequency_config_hash": freq_config_hash,
                "task_plan_hash": plan_hash,
            },
            "state_dict": state_dict,
        },
        state_dict_path,
    )
    try:
        results = _run_subprocess_tasks(
            tasks=tasks,
            stage_dir=stage_dir,
            state_dict_path=state_dict_path,
            workers=workers,
        )
    finally:
        if state_dict_path.exists():
            state_dict_path.unlink()

    passed_shards = sorted(
        [result for result in results if result.get("status") == "PASS"],
        key=lambda item: str(item["shard_id"]),
    )
    failed_shards = [result for result in results if result.get("status") != "PASS"]
    expected_total = sum(task.count for task in tasks)
    tmp_count = len(list((stage_dir / "artifacts" / "geometry" / "shards").glob("*.tmp")))
    marker_count = len(list((stage_dir / "artifacts" / "geometry" / "markers").glob("*.complete.json")))
    global_scan = _scan_global_ids(passed_shards, stage_dir) if not failed_shards else {
        "unique_observation_ids": 0,
        "duplicate_observation_id_count": -1,
        "object_type_counts": {"building": 0, "road": 0},
        "embedding_nonfinite_count": -1,
        "geometry_frequency_mask_false_count": -1,
        "geometry_frequency_mask_null_count": -1,
    }
    provenance_validation = validate_shard_provenance(
        passed_shards,
        expected_state_hash=state_hash,
        expected_architecture_hash=arch_hash,
        expected_upstream_inventory_hash=upstream_hash,
        expected_frequency_tensor_hash=freq_hash,
        expected_frequency_config_hash=freq_config_hash,
        expected_task_plan_hash=plan_hash,
    )
    row_count = sum(int(shard["row_count"]) for shard in passed_shards)
    aggregate_hash = hashlib.sha256(
        "".join(str(shard["sha256"]) for shard in passed_shards).encode("utf-8")
    ).hexdigest()
    validation = {
        "status": "PASS"
        if (
            not failed_shards
            and row_count == expected_total
            and global_scan["unique_observation_ids"] == expected_total
            and global_scan["duplicate_observation_id_count"] == 0
            and global_scan["embedding_nonfinite_count"] == 0
            and global_scan["geometry_frequency_mask_false_count"] == 0
            and global_scan["geometry_frequency_mask_null_count"] == 0
            and provenance_validation["valid"]
            and tmp_count == 0
            and marker_count == len(tasks)
        )
        else "FAIL",
        "expected_geometry_objects": expected_total,
        "geometry_rows": row_count,
        "missing": expected_total - global_scan["unique_observation_ids"],
        "duplicate_observation_id": global_scan["duplicate_observation_id_count"],
        "worker_exception": len(failed_shards),
        "object_failure": len(failed_shards),
        "nonfinite_embedding": global_scan["embedding_nonfinite_count"],
        "geometry_frequency_mask_false": global_scan["geometry_frequency_mask_false_count"],
        "geometry_frequency_mask_null": global_scan["geometry_frequency_mask_null_count"],
        "shard_provenance": provenance_validation,
        "tmp_file_count": tmp_count,
        "completion_marker_count": marker_count,
        "object_type_counts": global_scan["object_type_counts"],
    }
    shard_manifest = {
        "schema_id": SHARD_MANIFEST_SCHEMA_VERSION,
        "relative_path_policy": "stage_dir_relative",
        "task_count": len(tasks),
        "task_plan_hash": plan_hash,
        "workers": workers,
        "shards": passed_shards,
        "failed_shards": failed_shards,
        "aggregate_shard_hash": aggregate_hash,
        "provenance": {
            "state_dict_hash": state_hash,
            "architecture_hash": arch_hash,
            "upstream_inventory_hash": upstream_hash,
            "frequency_tensor_hash": freq_hash,
            "frequency_config_hash": freq_config_hash,
            "task_plan_hash": plan_hash,
            "schema_version": SHARD_SCHEMA_VERSION,
        },
    }
    return {
        "schema_id": "scene.m4.geometry_materialization_result.v1",
        "status": validation["status"],
        "workers": workers,
        "sample_limit": sample_limit,
        "elapsed_seconds": time.perf_counter() - started,
        "state_dict_hash": state_hash,
        "architecture_hash": arch_hash,
        "architecture": architecture,
        "frequency_tensor_hash": freq_hash,
        "frequency_config_hash": freq_config_hash,
        "task_plan_hash": plan_hash,
        "upstream_inventory_hash": upstream_hash,
        "upstream": upstream_inventory,
        "validation": validation,
        "shard_manifest": shard_manifest,
        "artifact_size_bytes": sum(int(shard["size_bytes"]) for shard in passed_shards),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="M4 geometry materialization worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--task-json", type=Path, required=True)
    worker.add_argument("--stage-dir", type=Path, required=True)
    worker.add_argument("--state-dict", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "worker":
        return _run_worker_from_files(args.task_json, args.stage_dir, args.state_dict)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
