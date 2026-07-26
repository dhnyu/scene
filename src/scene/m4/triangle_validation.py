"""M4.3A real-geometry Triangle backend validation."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import json
import math
import os
import platform
import random
import statistics
import time
from typing import Any

import numpy as np
import torch
from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from scene.m4.geometry_frequency import generate_frequency_grid
from scene.m4.polygon_errors import GeometryPrimitiveError
from scene.m4.polygon_fourier import polygon_fourier_transform
from scene.m4.triangle_backend import (
    AREA_EPSILON_M2,
    TRIANGLE_OPTIONS,
    triangle_dependency_info,
    triangulate_polygon_domain,
)

M3_RUN_ID = "20260726_025452_KST"
BUILDING_GPKG = Path(
    "outputs/m3/20260726_025452_KST/stages/M3.2/artifacts/observations/"
    "building/building_observations.gpkg"
)
BUILDING_ATTR = Path(
    "outputs/m3/20260726_025452_KST/stages/M3.2/artifacts/observations/"
    "building/building_attributes.parquet"
)
BUILDING_MANIFEST = Path("outputs/m3/20260726_025452_KST/stages/M3.2/stage_artifact_manifest.json")


@dataclass(frozen=True)
class StressConfig:
    """Configuration for the read-only M4.3A stress test."""

    sample_size: int = 1000
    workers: int = 40
    seed: int = 20260727
    scan_features: int = 25000
    exact_union_limit: int = 2000


def _dependency_environment() -> dict[str, object]:
    triangle_info = triangle_dependency_info()
    env: dict[str, object] = {
        "python": platform.python_version(),
        "python_executable": os.sys.executable,
        "triangle": triangle_info.version if triangle_info.import_ok else None,
        "triangle_import_ok": triangle_info.import_ok,
        "triangle_error": triangle_info.error,
        "triangle_options": TRIANGLE_OPTIONS,
        "numpy": np.__version__,
        "cuda": bool(torch.cuda.is_available()),
    }
    for name in ("shapely", "torch", "pyogrio", "geopandas"):
        try:
            module = __import__(name)
            env[name] = str(getattr(module, "__version__", "UNKNOWN"))
        except Exception as exc:  # pragma: no cover - environment dependent.
            env[name] = f"ERROR {type(exc).__name__}: {exc}"
    return env


def _load_building_sample(config: StressConfig) -> tuple[list[dict[str, Any]], dict[str, object]]:
    import pyogrio

    info = pyogrio.read_info(BUILDING_GPKG)
    features = int(info["features"])
    columns = [
        "observation_id",
        "scene_id",
        "geometry_status",
        "touches_scene_boundary",
        "observation_area_m2",
        "geometry_type",
    ]
    read_count = min(features, max(config.scan_features, config.sample_size))
    frame = pyogrio.read_dataframe(
        BUILDING_GPKG,
        layer="building_observation",
        columns=columns,
        max_features=read_count,
    )
    frame = frame[~frame.geometry.is_empty].copy()
    if len(frame) < config.sample_size:
        raise RuntimeError(
            f"building stress-test sample requires {config.sample_size} rows; "
            f"only {len(frame)} readable geometries found in first {read_count}"
        )

    def metrics(geom: Polygon | MultiPolygon) -> dict[str, object]:
        geoms = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        holes = sum(len(poly.interiors) for poly in geoms)
        vertices = 0
        for poly in geoms:
            vertices += len(poly.exterior.coords) - 1
            vertices += sum(len(ring.coords) - 1 for ring in poly.interiors)
        minx, miny, maxx, maxy = geom.bounds
        width = max(maxx - minx, 0.0)
        height = max(maxy - miny, 0.0)
        bbox_area = max(width * height, AREA_EPSILON_M2)
        aspect = max(width, height) / max(min(width, height), 1.0e-9)
        concave = any(abs(poly.convex_hull.area - poly.area) > AREA_EPSILON_M2 for poly in geoms)
        return {
            "component_count": len(geoms),
            "hole_count": holes,
            "vertex_count": vertices,
            "thin_score": aspect,
            "fill_ratio": float(geom.area / bbox_area),
            "concave": concave,
        }

    metric_rows = [metrics(geom) for geom in frame.geometry]
    for key in metric_rows[0].keys():
        frame[key] = [row[key] for row in metric_rows]
    frame["geometry_type_norm"] = frame.geometry_type.astype(str).str.upper()

    rng = random.Random(config.seed)
    selected_indices: set[int] = set()

    categories = {
        "simple_polygon": frame.index[(frame.geometry_type_norm == "POLYGON") & (frame.hole_count == 0) & (frame.vertex_count <= 8)].tolist(),
        "concave_polygon": frame.index[(frame.geometry_type_norm == "POLYGON") & (frame.concave)].tolist(),
        "polygon_with_holes": frame.index[(frame.geometry_type_norm == "POLYGON") & (frame.hole_count > 0)].tolist(),
        "multipolygon": frame.index[frame.geometry_type_norm == "MULTIPOLYGON"].tolist(),
        "multipolygon_with_holes": frame.index[(frame.geometry_type_norm == "MULTIPOLYGON") & (frame.hole_count > 0)].tolist(),
        "many_vertices": frame.sort_values("vertex_count", ascending=False).head(200).index.tolist(),
        "clipped": frame.index[frame.geometry_status.astype(str).isin(["clipped", "split_by_clip"]) | frame.touches_scene_boundary].tolist(),
        "thin_or_narrow": frame.sort_values("thin_score", ascending=False).head(200).index.tolist(),
        "small_area": frame.sort_values("observation_area_m2", ascending=True).head(200).index.tolist(),
        "complex_ring": frame.sort_values(["hole_count", "vertex_count"], ascending=False).head(200).index.tolist(),
    }

    for values in categories.values():
        chosen = values[:25]
        selected_indices.update(chosen)

    remaining = [idx for idx in frame.index.tolist() if idx not in selected_indices]
    rng.shuffle(remaining)
    selected_indices.update(remaining[: max(0, config.sample_size - len(selected_indices))])
    chosen = sorted(selected_indices)[: config.sample_size]
    sample = frame.loc[chosen].copy()

    records = []
    for _, row in sample.iterrows():
        records.append(
            {
                "observation_id": str(row.observation_id),
                "scene_id": str(row.scene_id),
                "geometry_type": str(row.geometry_type),
                "geometry_status": str(row.geometry_status),
                "touches_scene_boundary": bool(row.touches_scene_boundary),
                "area_m2": float(row.observation_area_m2),
                "component_count": int(row.component_count),
                "hole_count": int(row.hole_count),
                "vertex_count": int(row.vertex_count),
                "wkb_hex": row.geometry.wkb_hex,
            }
        )

    distribution = {
        name: int(sample.index.isin(values).sum())
        for name, values in categories.items()
    }
    sampling = {
        "source_features": features,
        "scanned_features": read_count,
        "sample_size": len(records),
        "seed": config.seed,
        "category_distribution": distribution,
        "geometry_type_counts": dict(Counter(sample.geometry_type.astype(str))),
        "hole_objects": int((sample.hole_count > 0).sum()),
        "multipolygon_objects": int((sample.geometry_type_norm == "MULTIPOLYGON").sum()),
    }
    return records, sampling


def _validate_one(record: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        geom = wkb.loads(bytes.fromhex(record["wkb_hex"]))
        original_wkb = geom.wkb
        if not isinstance(geom, (Polygon, MultiPolygon)):
            raise GeometryPrimitiveError(f"unexpected geometry type {geom.geom_type}")
        if geom.is_empty:
            raise GeometryPrimitiveError("geometry is empty")
        if not geom.is_valid:
            raise GeometryPrimitiveError("geometry invalid; repair is forbidden")
        triangles = triangulate_polygon_domain(geom)
        if geom.wkb != original_wkb:
            raise GeometryPrimitiveError("source geometry mutated")

        triangle_area_sum = sum(float(triangle.area) for triangle in triangles)
        union = unary_union(triangles)
        outside_area = float(union.difference(geom).area)
        gap_area = float(geom.difference(union).area)
        overlap_area = max(0.0, triangle_area_sum - float(union.area))
        hole_overlap = 0.0
        for polygon in (geom.geoms if isinstance(geom, MultiPolygon) else [geom]):
            for ring in polygon.interiors:
                hole_overlap += float(union.intersection(Polygon(ring)).area)

        fourier_start = time.perf_counter()
        omega = generate_frequency_grid(dtype=torch.float32)
        value = polygon_fourier_transform(geom, omega, dtype=torch.float32)
        fourier_elapsed = time.perf_counter() - fourier_start
        finite = bool(torch.isfinite(value.real).all() and torch.isfinite(value.imag).all())
        if not finite:
            raise GeometryPrimitiveError("fourier_complex has NaN or Inf")

        area_delta = abs(triangle_area_sum - float(geom.area))
        result = {
            "status": "PASS",
            "observation_id": record["observation_id"],
            "scene_id": record["scene_id"],
            "geometry_type": record["geometry_type"],
            "geometry_status": record["geometry_status"],
            "component_count": record["component_count"],
            "hole_count": record["hole_count"],
            "vertex_count": record["vertex_count"],
            "triangle_count": len(triangles),
            "area_m2": float(geom.area),
            "triangle_area_m2": triangle_area_sum,
            "area_delta_m2": area_delta,
            "area_relative_delta": area_delta / max(float(geom.area), AREA_EPSILON_M2),
            "outside_area_m2": outside_area,
            "gap_area_m2": gap_area,
            "overlap_area_m2": overlap_area,
            "hole_overlap_area_m2": hole_overlap,
            "source_mutation": False,
            "fourier_complex_shape": [int(value.shape[0])],
            "fourier_complex_dtype": str(value.dtype),
            "fourier_finite": finite,
            "triangulation_elapsed_sec": max(fourier_start - started, 0.0),
            "fourier_elapsed_sec": fourier_elapsed,
            "elapsed_sec": time.perf_counter() - started,
        }
        domain_ok = (
            area_delta <= AREA_EPSILON_M2
            and outside_area <= AREA_EPSILON_M2
            and gap_area <= AREA_EPSILON_M2
            and overlap_area <= AREA_EPSILON_M2
            and hole_overlap <= AREA_EPSILON_M2
        )
        if not domain_ok:
            result["status"] = "FAIL"
            result["failure_category"] = "domain_preservation"
        return result
    except Exception as exc:
        return {
            "status": "FAIL",
            "observation_id": record.get("observation_id"),
            "scene_id": record.get("scene_id"),
            "geometry_type": record.get("geometry_type"),
            "geometry_status": record.get("geometry_status"),
            "component_count": record.get("component_count"),
            "hole_count": record.get("hole_count"),
            "vertex_count": record.get("vertex_count"),
            "failure_category": "exception",
            "exception_class": type(exc).__name__,
            "exception_message": str(exc),
            "elapsed_sec": time.perf_counter() - started,
        }


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "p95": float(ordered[p95_index]),
        "max": float(max(values)),
    }


def run_triangle_backend_stress(
    *,
    output_dir: Path,
    config: StressConfig | None = None,
) -> dict[str, object]:
    """Run the M4.3A dependency, sampling and 40-worker stress validation."""

    config = config or StressConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    environment = _dependency_environment()
    if not environment["triangle_import_ok"]:
        result = {
            "status": "FAIL",
            "blocked": True,
            "failure_reason": "triangle dependency is not importable in the active project Python",
            "dependency_environment": environment,
            "workers": config.workers,
            "auto_continue": False,
        }
        _write_stress_outputs(output_dir, result, [])
        return result
    if config.workers != 40:
        result = {
            "status": "FAIL",
            "blocked": True,
            "failure_reason": f"M4.3A requires workers=40; observed {config.workers}",
            "dependency_environment": environment,
            "workers": config.workers,
            "auto_continue": False,
        }
        _write_stress_outputs(output_dir, result, [])
        return result

    records, sampling = _load_building_sample(config)
    result_by_id: dict[str, dict[str, Any]] = {}
    worker_exceptions = 0
    with ProcessPoolExecutor(max_workers=config.workers) as executor:
        futures = [executor.submit(_validate_one, record) for record in records]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive aggregation.
                worker_exceptions += 1
                result = {
                    "status": "FAIL",
                    "failure_category": "worker_exception",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                }
            observation_id = str(result.get("observation_id"))
            if observation_id in result_by_id:
                result["status"] = "FAIL"
                result["failure_category"] = "duplicate_result"
            result_by_id[observation_id] = result

    results = [result_by_id.get(record["observation_id"]) for record in records]
    missing = sum(1 for value in results if value is None)
    completed = [value for value in results if value and value.get("status") == "PASS"]
    failed = [value for value in results if value and value.get("status") != "PASS"]
    duplicate_observation_ids = len(result_by_id) != len(records)
    failure_counts = dict(Counter(str(row.get("failure_category", "unknown")) for row in failed))

    metrics = {
        "input_observations": len(records),
        "completed": len(completed),
        "failed": len(failed),
        "missing": missing,
        "duplicated": int(duplicate_observation_ids),
        "worker_exceptions": worker_exceptions,
        "elapsed_sec": time.perf_counter() - started,
        "observations_per_sec": len(records) / max(time.perf_counter() - started, 1.0e-9),
        "triangulation_time_sec": _stats([float(row["triangulation_elapsed_sec"]) for row in completed]),
        "fourier_time_sec": _stats([float(row["fourier_elapsed_sec"]) for row in completed]),
        "triangle_count": _stats([float(row["triangle_count"]) for row in completed]),
        "max_area_delta_m2": max((float(row["area_delta_m2"]) for row in completed), default=None),
        "max_outside_area_m2": max((float(row["outside_area_m2"]) for row in completed), default=None),
        "max_gap_area_m2": max((float(row["gap_area_m2"]) for row in completed), default=None),
        "max_overlap_area_m2": max((float(row["overlap_area_m2"]) for row in completed), default=None),
        "max_hole_overlap_area_m2": max((float(row["hole_overlap_area_m2"]) for row in completed), default=None),
    }
    domain_pass = (
        len(completed) == len(records)
        and missing == 0
        and not duplicate_observation_ids
        and worker_exceptions == 0
        and metrics["max_area_delta_m2"] is not None
        and float(metrics["max_area_delta_m2"]) <= AREA_EPSILON_M2
        and float(metrics["max_outside_area_m2"]) <= AREA_EPSILON_M2
        and float(metrics["max_gap_area_m2"]) <= AREA_EPSILON_M2
        and float(metrics["max_overlap_area_m2"]) <= AREA_EPSILON_M2
        and float(metrics["max_hole_overlap_area_m2"]) <= AREA_EPSILON_M2
    )
    result = {
        "status": "PASS" if domain_pass else "FAIL",
        "blocked": False,
        "dependency_environment": environment,
        "m3_source": {
            "run_id": M3_RUN_ID,
            "building_observation_gpkg": str(BUILDING_GPKG),
            "building_attributes_parquet": str(BUILDING_ATTR),
            "stage_artifact_manifest": str(BUILDING_MANIFEST),
            "gpkg_size_bytes": BUILDING_GPKG.stat().st_size if BUILDING_GPKG.exists() else None,
        },
        "sampling": sampling,
        "parallel_execution": {
            "workers": config.workers,
            "backend": "concurrent.futures.ProcessPoolExecutor",
            "worker_1_vs_n_exact_parity_required": False,
        },
        "metrics": metrics,
        "failure_counts": failure_counts,
        "representative_failures": failed[:20],
        "auto_continue": False,
    }
    _write_stress_outputs(output_dir, result, failed[:500])
    return result


def _write_stress_outputs(
    output_dir: Path,
    summary: dict[str, object],
    failures: list[dict[str, Any]],
) -> None:
    (output_dir / "triangle_stress_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "triangle_stress_failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
