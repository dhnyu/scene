#!/usr/bin/env python
"""Read-only audit of configured vector, Parquet, raster, and cross-source data."""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pyogrio
import shapely
from osgeo import gdal, ogr
from pyproj import CRS

from audit_utils import (
    load_configs,
    now_kst,
    quick_fingerprint,
    raw_dir,
    setup_logging,
    sha256_file,
    source_state,
    write_csv,
    write_json,
)

gdal.UseExceptions()
LOGGER = setup_logging("data_audit")

FILE_FIELDS = [
    "checked_at_kst", "source_path", "exists", "readable", "file_format",
    "size_bytes", "mtime_kst", "sidecar_files", "sha256_calculated", "sha256",
    "quick_fingerprint", "hash_elapsed_seconds", "error",
]
LAYER_FIELDS = [
    "checked_at_kst", "source_path", "layer_name", "feature_count",
    "attribute_column_count", "geometry_column", "declared_geometry_type",
    "observed_geometry_types", "crs", "epsg", "axis_info", "unit",
    "bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy", "has_z_count",
    "has_m_count", "null_geometry_count", "empty_geometry_count",
    "valid_geometry_count", "invalid_geometry_count", "invalid_reasons",
    "geometry_collection_count", "multipart_count", "multipart_rate",
    "self_intersection_count", "duplicate_geometry_row_count",
    "zero_measure_count", "perimeter_summary", "small_measure_threshold",
    "small_measure_count",
    "large_measure_threshold", "large_measure_count", "coordinate_nonfinite_count",
    "unexpected_geometry_count", "spatial_index_present", "inspection_scope",
    "elapsed_seconds", "error",
]
COLUMN_FIELDS = [
    "checked_at_kst", "source_path", "source_kind", "layer_name", "column_name",
    "data_type", "row_count", "null_count", "null_rate", "distinct_count",
    "duplicate_nonnull_count", "empty_string_count", "whitespace_only_count",
    "pseudo_missing_count", "leading_zero_count", "min", "max", "mean", "std",
    "q01", "q25", "q50", "q75", "q99", "negative_count", "zero_count",
    "nonfinite_count", "id_candidate", "join_candidate",
    "compression", "notes",
]
JOIN_FIELDS = [
    "checked_at_kst", "object_type", "geometry_source", "attribute_source",
    "geometry_key", "attribute_key", "comparison", "geometry_rows",
    "attribute_rows", "geometry_null_count", "attribute_null_count",
    "geometry_distinct_count", "attribute_distinct_count", "intersection_distinct",
    "geometry_match_count", "attribute_match_count", "geometry_match_rate",
    "attribute_match_rate", "geometry_duplicate_key_rows",
    "attribute_duplicate_key_rows", "cardinality", "classification",
    "leading_zero_count_geometry", "leading_zero_count_attribute",
    "whitespace_count_geometry", "whitespace_count_attribute",
    "normalization_changed_match_rate", "recommended", "notes",
]
RASTER_FIELDS = [
    "checked_at_kst", "source_path", "driver", "crs", "epsg", "width", "height",
    "band_count", "pixel_count", "data_type", "nodata", "resolution_x",
    "resolution_y", "origin_x", "origin_y", "extent_minx", "extent_miny",
    "extent_maxx", "extent_maxy", "north_up", "affine_transform", "compression",
    "tiled", "block_x", "block_y", "overview_count", "valid_cell_count",
    "nodata_cell_count", "negative_cell_count", "minimum", "maximum", "mean",
    "std", "q01", "q25",
    "q50", "q75", "q99", "boundary_valid_cell_count", "boundary_minimum",
    "boundary_maximum", "boundary_mean", "boundary_std",
    "boundary_negative_cell_count", "unique_class_count",
    "class_counts", "zero_code_count", "legend_present", "scene_patch_pixels",
    "inspection_scope", "elapsed_seconds", "error",
]


def safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator / denominator) if denominator else None


def numeric_summary(values: np.ndarray) -> dict[str, Any]:
    array = np.asarray(values)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {key: None for key in (
            "min", "max", "mean", "std", "q00", "q01", "q25", "q50", "q75",
            "q99", "q100"
        )}
    quantiles = np.quantile(array, [0, 0.01, 0.25, 0.5, 0.75, 0.99, 1])
    return {
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
        "std": float(np.std(array)),
        "q00": float(quantiles[0]),
        "q01": float(quantiles[1]),
        "q25": float(quantiles[2]),
        "q50": float(quantiles[3]),
        "q75": float(quantiles[4]),
        "q99": float(quantiles[5]),
        "q100": float(quantiles[6]),
    }


def file_format(path: Path) -> str:
    return {
        ".gpkg": "GeoPackage",
        ".parquet": "Parquet",
        ".tif": "GeoTIFF",
        ".tiff": "GeoTIFF",
    }.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "unknown")


def sidecars(path: Path) -> list[str]:
    candidates = []
    for sibling in path.parent.iterdir():
        if sibling == path or not sibling.is_file():
            continue
        if sibling.stem == path.stem or sibling.name.startswith(path.name + "-"):
            candidates.append(str(sibling.resolve()))
    return sorted(candidates)


def inventory_file(path: Path, full_hash_max: int, sample_bytes: int) -> dict[str, Any]:
    checked = now_kst()
    row = {
        "checked_at_kst": checked,
        "source_path": str(path.resolve()),
        "exists": path.exists(),
        "readable": path.is_file() and os.access(path, os.R_OK),
        "file_format": file_format(path),
        "sidecar_files": sidecars(path) if path.exists() else [],
        "error": None,
    }
    if not path.exists():
        row["error"] = "파일 없음"
        return row
    stat = path.stat()
    row.update({
        "size_bytes": stat.st_size,
        "mtime_kst": pd.Timestamp(stat.st_mtime, unit="s", tz="UTC").tz_convert(
            "Asia/Seoul"
        ).isoformat(),
    })
    started = time.monotonic()
    try:
        row["quick_fingerprint"] = quick_fingerprint(path, sample_bytes)
        if stat.st_size <= full_hash_max:
            row["sha256"] = sha256_file(path)
            row["sha256_calculated"] = True
        else:
            row["sha256"] = None
            row["sha256_calculated"] = False
            row["error"] = (
                f"full SHA-256 생략: {stat.st_size} bytes가 설정 한도 "
                f"{full_hash_max} bytes를 초과"
            )
    except Exception as exc:
        row["sha256"] = None
        row["sha256_calculated"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["hash_elapsed_seconds"] = time.monotonic() - started
    return row


def pandas_column_stats(
    series: pd.Series,
    source_path: Path,
    source_kind: str,
    layer: str | None,
    pseudo_tokens: set[str],
    join_names: set[str],
) -> dict[str, Any]:
    count = len(series)
    null_count = int(series.isna().sum())
    distinct = int(series.nunique(dropna=True))
    row: dict[str, Any] = {
        "checked_at_kst": now_kst(),
        "source_path": str(source_path),
        "source_kind": source_kind,
        "layer_name": layer,
        "column_name": series.name,
        "data_type": str(series.dtype),
        "row_count": count,
        "null_count": null_count,
        "null_rate": safe_rate(null_count, count),
        "distinct_count": distinct,
        "duplicate_nonnull_count": max(0, count - null_count - distinct),
        "id_candidate": is_id_name(str(series.name)) and null_count == 0
        and distinct / max(1, count) >= 0.95,
        "join_candidate": str(series.name) in join_names,
        "notes": None,
    }
    if pd.api.types.is_string_dtype(series.dtype) or series.dtype == object:
        text = series.astype("string")
        stripped = text.str.strip()
        row["empty_string_count"] = int((text == "").sum())
        row["whitespace_only_count"] = int(((text != "") & (stripped == "")).sum())
        row["pseudo_missing_count"] = int(
            stripped.str.upper().isin({token.upper() for token in pseudo_tokens}).sum()
        )
        row["leading_zero_count"] = int(
            stripped.str.match(r"^0[0-9]+$", na=False).sum()
        )
    elif pd.api.types.is_numeric_dtype(series.dtype):
        values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        summary = numeric_summary(values)
        finite = values[np.isfinite(values)]
        row.update({
            "min": summary["min"], "max": summary["max"], "mean": summary["mean"],
            "std": summary["std"], "q01": summary["q01"], "q25": summary["q25"],
            "q50": summary["q50"], "q75": summary["q75"], "q99": summary["q99"],
            "negative_count": int(np.sum(finite < 0)),
            "zero_count": int(np.sum(finite == 0)),
            "nonfinite_count": int(
                np.sum(~np.isfinite(values) & ~series.isna().to_numpy())
            ),
        })
    return row


def is_id_name(name: str) -> bool:
    upper = name.upper()
    return (
        upper == "ID"
        or upper.endswith("_ID")
        or upper in {"LINK_ID", "NODE_ID", "F_NODE", "T_NODE", "BUILDING_ID"}
        or "IDENTIFIER" in upper
    )


def crs_details(crs_value: Any) -> dict[str, Any]:
    if crs_value is None:
        return {"crs": None, "epsg": None, "axis_info": None, "unit": None, "wkt": None}
    crs = CRS.from_user_input(crs_value)
    return {
        "crs": crs.to_string(),
        "epsg": crs.to_epsg(),
        "axis_info": [
            {"name": axis.name, "direction": axis.direction, "unit": axis.unit_name}
            for axis in crs.axis_info
        ],
        "unit": crs.axis_info[0].unit_name if crs.axis_info else None,
        "wkt": crs.to_wkt(),
    }


def geometry_measure(geoms: Any, family: str) -> np.ndarray:
    if family in {"Polygon", "MultiPolygon"}:
        return np.asarray(shapely.area(geoms), dtype=float)
    if family in {"LineString", "MultiLineString"}:
        return np.asarray(shapely.length(geoms), dtype=float)
    return np.full(len(geoms), np.nan)


def vector_layer_audit(
    path: Path,
    layer: str,
    pseudo_tokens: set[str],
    join_names: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], gpd.GeoDataFrame, dict[str, Any]]:
    started = time.monotonic()
    info = pyogrio.read_info(path, layer=layer, force_total_bounds=True)
    LOGGER.info("Reading full vector layer: %s:%s", path.name, layer)
    gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
    geometry_name = gdf.geometry.name
    geoms = gdf.geometry.array
    row_count = len(gdf)
    null_mask = np.asarray(shapely.is_missing(geoms), dtype=bool)
    empty_mask = np.asarray(shapely.is_empty(geoms), dtype=bool)
    valid_mask = np.asarray(shapely.is_valid(geoms), dtype=bool)
    invalid_mask = ~valid_mask & ~null_mask
    reasons: dict[str, int] = {}
    if invalid_mask.any():
        reason_values = shapely.is_valid_reason(geoms[invalid_mask])
        reasons = dict(Counter(map(str, reason_values)).most_common(20))
    types = np.asarray(shapely.get_type_id(geoms), dtype=int)
    type_names = np.asarray(shapely.get_type_id(geoms), dtype=int)
    type_name_values = np.asarray([
        None if geom is None else geom.geom_type for geom in geoms
    ], dtype=object)
    type_counts = Counter(str(value) for value in type_name_values if value is not None)
    declared = str(info["geometry_type"])
    expected = {declared.replace(" ", "")}
    unexpected = sum(
        count for name, count in type_counts.items() if name.replace(" ", "") not in expected
    )
    multipart_mask = np.asarray([
        bool(name and str(name).startswith("Multi")) for name in type_name_values
    ])
    geometry_collection_count = int(np.sum(types == 7))
    duplicate_geometry_rows = 0
    nonnull_nonempty = ~(null_mask | empty_mask)
    if nonnull_nonempty.any():
        wkb = pd.Series(
            shapely.to_wkb(geoms[nonnull_nonempty], hex=False),
            dtype=object,
        )
        duplicate_geometry_rows = int(wkb.duplicated(keep=False).sum())
    coords = shapely.get_coordinates(geoms, include_z=False)
    nonfinite_count = int((~np.isfinite(coords)).sum()) if coords.size else 0
    has_z = int(np.sum(shapely.has_z(geoms)))
    try:
        has_m = int(np.sum(shapely.has_m(geoms)))
    except Exception:
        has_m = 0
    measures = geometry_measure(geoms, declared.replace(" ", ""))
    finite_measures = measures[np.isfinite(measures)]
    measure_summary = numeric_summary(finite_measures)
    zero_measure = int(np.sum(finite_measures <= 0)) if finite_measures.size else 0
    small_threshold = measure_summary["q01"]
    large_threshold = measure_summary["q99"]
    small_count = int(np.sum(finite_measures < small_threshold)) if small_threshold is not None else 0
    large_count = int(np.sum(finite_measures > large_threshold)) if large_threshold is not None else 0
    perimeter_summary = (
        numeric_summary(np.asarray(shapely.length(geoms), dtype=float))
        if declared.replace(" ", "") in {"Polygon", "MultiPolygon"} else None
    )
    crs = crs_details(gdf.crs)
    bounds = info["total_bounds"]
    layer_row = {
        "checked_at_kst": now_kst(),
        "source_path": str(path),
        "layer_name": layer,
        "feature_count": row_count,
        "attribute_column_count": len(gdf.columns) - 1,
        "geometry_column": info.get("geometry_name", geometry_name),
        "declared_geometry_type": declared,
        "observed_geometry_types": dict(type_counts),
        "crs": crs["crs"],
        "epsg": crs["epsg"],
        "crs_wkt": crs["wkt"],
        "axis_info": crs["axis_info"],
        "unit": crs["unit"],
        "bbox_minx": float(bounds[0]), "bbox_miny": float(bounds[1]),
        "bbox_maxx": float(bounds[2]), "bbox_maxy": float(bounds[3]),
        "has_z_count": has_z, "has_m_count": has_m,
        "null_geometry_count": int(null_mask.sum()),
        "empty_geometry_count": int(empty_mask.sum()),
        "valid_geometry_count": int(valid_mask.sum()),
        "invalid_geometry_count": int(invalid_mask.sum()),
        "invalid_reasons": reasons,
        "geometry_collection_count": geometry_collection_count,
        "multipart_count": int(multipart_mask.sum()),
        "multipart_rate": safe_rate(int(multipart_mask.sum()), row_count),
        "self_intersection_count": sum(
            count for reason, count in reasons.items() if "self-intersection" in reason.lower()
        ),
        "duplicate_geometry_row_count": duplicate_geometry_rows,
        "zero_measure_count": zero_measure,
        "measure_summary": measure_summary,
        "perimeter_summary": perimeter_summary,
        "vertex_count_summary": numeric_summary(
            np.asarray(shapely.get_num_coordinates(geoms), dtype=float)
        ),
        "part_count_summary": numeric_summary(
            np.asarray(shapely.get_num_geometries(geoms), dtype=float)
        ),
        "small_measure_threshold": small_threshold,
        "small_measure_count": small_count,
        "large_measure_threshold": large_threshold,
        "large_measure_count": large_count,
        "coordinate_nonfinite_count": nonfinite_count,
        "unexpected_geometry_count": int(unexpected),
        "spatial_index_present": bool(gdf.has_sindex),
        "inspection_scope": "전체",
        "elapsed_seconds": time.monotonic() - started,
        "error": None,
    }
    column_rows = [
        pandas_column_stats(
            gdf[column], path, "GeoPackage", layer, pseudo_tokens, join_names
        )
        for column in gdf.columns if column != geometry_name
    ]
    derived = {
        "measure_summary": measure_summary,
        "vertex_count_summary": layer_row["vertex_count_summary"],
        "type_counts": dict(type_counts),
    }
    return layer_row, column_rows, gdf, derived


def parquet_column_stats(
    parquet_file: pq.ParquetFile,
    path: Path,
    name: str,
    pseudo_tokens: set[str],
    join_names: set[str],
) -> dict[str, Any]:
    array = parquet_file.read(columns=[name]).column(0).combine_chunks()
    count = len(array)
    null_count = array.null_count
    distinct = int(pc.count_distinct(array, mode="only_valid").as_py())
    field = parquet_file.schema_arrow.field(name)
    compression = sorted({
        parquet_file.metadata.row_group(i).column(
            parquet_file.schema_arrow.get_field_index(name)
        ).compression
        for i in range(parquet_file.metadata.num_row_groups)
    })
    row: dict[str, Any] = {
        "checked_at_kst": now_kst(),
        "source_path": str(path),
        "source_kind": "Parquet",
        "layer_name": None,
        "column_name": name,
        "data_type": str(field.type),
        "row_count": count,
        "null_count": null_count,
        "null_rate": safe_rate(null_count, count),
        "distinct_count": distinct,
        "duplicate_nonnull_count": max(0, count - null_count - distinct),
        "id_candidate": is_id_name(name) and null_count == 0
        and distinct / max(1, count) >= 0.95,
        "join_candidate": name in join_names,
        "compression": compression,
        "notes": None,
    }
    if pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
        text = pc.cast(array, pa.string())
        stripped = pc.utf8_trim_whitespace(text)
        row["empty_string_count"] = int(pc.sum(pc.equal(text, "")).as_py() or 0)
        row["whitespace_only_count"] = int(
            pc.sum(pc.and_(pc.not_equal(text, ""), pc.equal(stripped, ""))).as_py() or 0
        )
        pseudo = pc.is_in(
            pc.utf8_upper(stripped),
            value_set=pa.array(sorted({token.upper() for token in pseudo_tokens})),
        )
        row["pseudo_missing_count"] = int(pc.sum(pseudo).as_py() or 0)
        matched = pc.match_substring_regex(stripped, r"^0[0-9]+$")
        row["leading_zero_count"] = int(pc.sum(matched).as_py() or 0)
    elif pa.types.is_integer(field.type) or pa.types.is_floating(field.type):
        values = array.to_numpy(zero_copy_only=False).astype(float, copy=False)
        summary = numeric_summary(values)
        finite = values[np.isfinite(values)]
        row.update({
            "min": summary["min"], "max": summary["max"], "mean": summary["mean"],
            "std": summary["std"], "q01": summary["q01"], "q25": summary["q25"],
            "q50": summary["q50"], "q75": summary["q75"], "q99": summary["q99"],
            "negative_count": int(np.sum(finite < 0)),
            "zero_count": int(np.sum(finite == 0)),
            "nonfinite_count": max(0, int(np.sum(~np.isfinite(values))) - null_count),
        })
    return row


def audit_parquet(
    path: Path,
    pseudo_tokens: set[str],
    join_names: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.monotonic()
    parquet_file = pq.ParquetFile(path)
    columns = []
    for name in parquet_file.schema_arrow.names:
        LOGGER.info("Scanning Parquet column: %s:%s", path.name, name)
        columns.append(parquet_column_stats(
            parquet_file, path, name, pseudo_tokens, join_names
        ))
    schema_types = [field.type for field in parquet_file.schema_arrow]
    summary = {
        "checked_at_kst": now_kst(),
        "source_path": str(path),
        "row_count": parquet_file.metadata.num_rows,
        "column_count": parquet_file.metadata.num_columns,
        "row_group_count": parquet_file.metadata.num_row_groups,
        "schema": str(parquet_file.schema_arrow),
        "compression_codecs": sorted({
            parquet_file.metadata.row_group(i).column(j).compression
            for i in range(parquet_file.metadata.num_row_groups)
            for j in range(parquet_file.metadata.num_columns)
        }),
        "nested_or_dictionary_types": [
            str(value) for value in schema_types
            if pa.types.is_list(value) or pa.types.is_large_list(value)
            or pa.types.is_struct(value) or pa.types.is_dictionary(value)
        ],
        "full_row_duplicate_scan": "미실행: audit.yaml에서 비활성화; 안정적 ID 중복으로 대체",
        "utf8_decoding": "성공",
        "elapsed_seconds": time.monotonic() - started,
        "error": None,
    }
    return summary, columns


def normalized_key(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def join_audit_row(
    object_type: str,
    geometry_source: Path,
    attribute_source: Path,
    geometry_key: str,
    attribute_key: str,
    geometry_values: pd.Series,
    attribute_values: pd.Series,
    comparison: str,
    recommended: bool,
) -> dict[str, Any]:
    raw_g = geometry_values.astype("string")
    raw_a = attribute_values.astype("string")
    g = normalized_key(geometry_values)
    a = normalized_key(attribute_values)
    g_null = int(g.isna().sum())
    a_null = int(a.isna().sum())
    g_nonnull = g.dropna()
    a_nonnull = a.dropna()
    g_unique = pd.Index(g_nonnull.unique())
    a_unique = pd.Index(a_nonnull.unique())
    intersection = g_unique.intersection(a_unique)
    g_match = int(g.isin(a_unique).sum())
    a_match = int(a.isin(g_unique).sum())
    raw_g_unique = pd.Index(raw_g.dropna().unique())
    raw_a_unique = pd.Index(raw_a.dropna().unique())
    raw_g_match = int(raw_g.isin(raw_a_unique).sum())
    normalized_changed = safe_rate(g_match, len(g)) != safe_rate(raw_g_match, len(raw_g))
    g_dup_rows = int(g_nonnull.duplicated(keep=False).sum())
    a_dup_rows = int(a_nonnull.duplicated(keep=False).sum())
    if g_dup_rows == 0 and a_dup_rows == 0:
        cardinality = "one-to-one"
    elif g_dup_rows == 0:
        cardinality = "one-to-many"
    elif a_dup_rows == 0:
        cardinality = "many-to-one"
    else:
        cardinality = "many-to-many"
    g_rate = safe_rate(g_match, len(g))
    a_rate = safe_rate(a_match, len(a))
    if cardinality == "one-to-one" and g_rate == 1.0 and a_rate == 1.0:
        classification = "확정적 1:1 조인 가능"
    elif cardinality == "one-to-one" and min(g_rate or 0, a_rate or 0) >= 0.99:
        classification = "거의 1:1이나 일부 예외 존재"
    elif normalized_changed:
        classification = "ID 정규화 필요"
    elif g_rate and a_rate and min(g_rate, a_rate) >= 0.9:
        classification = "복합키 필요"
    else:
        classification = "신뢰 가능한 조인 키를 찾지 못함"
    return {
        "checked_at_kst": now_kst(),
        "object_type": object_type,
        "geometry_source": str(geometry_source),
        "attribute_source": str(attribute_source),
        "geometry_key": geometry_key,
        "attribute_key": attribute_key,
        "comparison": comparison,
        "geometry_rows": len(g),
        "attribute_rows": len(a),
        "geometry_null_count": g_null,
        "attribute_null_count": a_null,
        "geometry_distinct_count": len(g_unique),
        "attribute_distinct_count": len(a_unique),
        "intersection_distinct": len(intersection),
        "geometry_match_count": g_match,
        "attribute_match_count": a_match,
        "geometry_match_rate": g_rate,
        "attribute_match_rate": a_rate,
        "geometry_duplicate_key_rows": g_dup_rows,
        "attribute_duplicate_key_rows": a_dup_rows,
        "cardinality": cardinality,
        "classification": classification,
        "leading_zero_count_geometry": int(g.str.match(r"^0[0-9]+$", na=False).sum()),
        "leading_zero_count_attribute": int(a.str.match(r"^0[0-9]+$", na=False).sum()),
        "whitespace_count_geometry": int((raw_g != g).sum()),
        "whitespace_count_attribute": int((raw_a != a).sum()),
        "normalization_changed_match_rate": normalized_changed,
        "recommended": recommended,
        "notes": "비교 정규화는 문자열 변환과 양끝 공백 제거만 적용; 원본 미변경",
    }


def bbox_polygon(extent: list[float] | tuple[float, float, float, float]) -> Any:
    return shapely.box(extent[0], extent[1], extent[2], extent[3])


def coverage_metrics(geoms: Any, area: Any) -> dict[str, Any]:
    count = len(geoms)
    reps = shapely.point_on_surface(geoms)
    representative_inside = np.asarray(shapely.covers(area, reps), dtype=bool)
    try:
        fully_inside = np.asarray(shapely.covers(area, geoms), dtype=bool)
        full_error = None
    except Exception as exc:
        fully_inside = np.zeros(count, dtype=bool)
        full_error = f"{type(exc).__name__}: {exc}"
    intersects = np.asarray(shapely.intersects(area, geoms), dtype=bool)
    return {
        "row_count": count,
        "representative_inside_count": int(representative_inside.sum()),
        "representative_inside_rate": safe_rate(representative_inside.sum(), count),
        "fully_inside_count": int(fully_inside.sum()),
        "fully_inside_rate": safe_rate(fully_inside.sum(), count),
        "intersects_count": int(intersects.sum()),
        "intersects_rate": safe_rate(intersects.sum(), count),
        "full_coverage_error": full_error,
    }


def endpoint_integrity(
    links: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    tolerances: Iterable[float],
) -> dict[str, Any]:
    node_ids = nodes["NODE_ID"].astype("string")
    node_duplicate_rows = int(node_ids[node_ids.notna()].duplicated(keep=False).sum())
    node_lookup = pd.Series(nodes.geometry.array, index=node_ids).groupby(level=0).first()
    f_ids = links["F_NODE"].astype("string")
    t_ids = links["T_NODE"].astype("string")
    f_match = f_ids.isin(node_lookup.index)
    t_match = t_ids.isin(node_lookup.index)
    start_points = shapely.get_point(links.geometry.array, 0)
    end_points = shapely.get_point(links.geometry.array, -1)
    f_node_geom = np.asarray([
        node_lookup.get(value, None) if value is not pd.NA else None for value in f_ids
    ], dtype=object)
    t_node_geom = np.asarray([
        node_lookup.get(value, None) if value is not pd.NA else None for value in t_ids
    ], dtype=object)
    f_dist = np.asarray(shapely.distance(start_points, f_node_geom), dtype=float)
    t_dist = np.asarray(shapely.distance(end_points, t_node_geom), dtype=float)
    tolerance_results = {}
    for tolerance in tolerances:
        pair_ok = (f_dist <= tolerance) & (t_dist <= tolerance)
        endpoint_ok = np.concatenate([f_dist <= tolerance, t_dist <= tolerance])
        tolerance_results[str(tolerance)] = {
            "matched_endpoint_count": int(np.nansum(endpoint_ok)),
            "endpoint_match_rate": safe_rate(int(np.nansum(endpoint_ok)), len(endpoint_ok)),
            "matched_link_count": int(np.nansum(pair_ok)),
            "link_both_endpoints_match_rate": safe_rate(int(np.nansum(pair_ok)), len(pair_ok)),
        }
    referenced = pd.Index(pd.concat([f_ids, t_ids], ignore_index=True).dropna().unique())
    isolated = ~node_ids.isin(referenced)
    return {
        "link_count": len(links),
        "node_count": len(nodes),
        "node_id_duplicate_rows": node_duplicate_rows,
        "from_id_match_count": int(f_match.sum()),
        "from_id_match_rate": safe_rate(f_match.sum(), len(links)),
        "to_id_match_count": int(t_match.sum()),
        "to_id_match_rate": safe_rate(t_match.sum(), len(links)),
        "both_id_match_count": int((f_match & t_match).sum()),
        "both_id_match_rate": safe_rate((f_match & t_match).sum(), len(links)),
        "self_loop_id_count": int((f_ids == t_ids).sum()),
        "unreferenced_node_count": int(isolated.sum()),
        "unreferenced_node_rate": safe_rate(isolated.sum(), len(nodes)),
        "endpoint_distance_m": {
            "from": numeric_summary(f_dist),
            "to": numeric_summary(t_dist),
        },
        "tolerance_results": tolerance_results,
    }


def raster_mask_for_boundary(dataset: gdal.Dataset, boundary_path: Path) -> np.ndarray:
    memory = gdal.GetDriverByName("MEM").Create(
        "", dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_Byte
    )
    memory.SetGeoTransform(dataset.GetGeoTransform())
    memory.SetProjection(dataset.GetProjection())
    source = ogr.Open(str(boundary_path), 0)
    if source is None:
        raise RuntimeError(f"Cannot open boundary: {boundary_path}")
    layer = source.GetLayer(0)
    gdal.RasterizeLayer(memory, [1], layer, burn_values=[1])
    mask = memory.GetRasterBand(1).ReadAsArray().astype(bool)
    source = None
    memory = None
    return mask


def audit_raster(path: Path, boundary_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    dataset = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)
    if dataset is None:
        raise RuntimeError(f"GDAL could not open raster: {path}")
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    valid_mask = np.isfinite(array)
    if nodata is not None:
        valid_mask &= array != nodata
    valid_values = array[valid_mask].astype(np.float64, copy=False)
    summary = numeric_summary(valid_values)
    geotransform = dataset.GetGeoTransform()
    minx = geotransform[0]
    maxy = geotransform[3]
    maxx = minx + dataset.RasterXSize * geotransform[1] + dataset.RasterYSize * geotransform[2]
    miny = maxy + dataset.RasterXSize * geotransform[4] + dataset.RasterYSize * geotransform[5]
    boundary_mask = raster_mask_for_boundary(dataset, boundary_path)
    boundary_valid = boundary_mask & valid_mask
    boundary_values = array[boundary_valid].astype(np.float64, copy=False)
    boundary_summary = numeric_summary(boundary_values)
    structure = dataset.GetMetadata("IMAGE_STRUCTURE")
    block_x, block_y = band.GetBlockSize()
    crs = crs_details(dataset.GetProjection())
    is_landcover = "landcover" in path.name.lower()
    class_counts: dict[str, int] = {}
    if is_landcover:
        unique, counts = np.unique(array[valid_mask], return_counts=True)
        class_counts = {str(value.item()): int(count) for value, count in zip(unique, counts)}
    legend_candidates = [
        candidate for candidate in path.parent.iterdir()
        if candidate.is_file()
        and "legend" in candidate.name.lower()
        and candidate != path
    ]
    resolution_x = abs(float(geotransform[1]))
    resolution_y = abs(float(geotransform[5]))
    row = {
        "checked_at_kst": now_kst(),
        "source_path": str(path),
        "driver": dataset.GetDriver().ShortName,
        "crs": crs["crs"],
        "epsg": crs["epsg"],
        "crs_wkt": crs["wkt"],
        "width": dataset.RasterXSize,
        "height": dataset.RasterYSize,
        "band_count": dataset.RasterCount,
        "pixel_count": dataset.RasterXSize * dataset.RasterYSize,
        "data_type": gdal.GetDataTypeName(band.DataType),
        "nodata": nodata,
        "resolution_x": resolution_x,
        "resolution_y": resolution_y,
        "origin_x": geotransform[0],
        "origin_y": geotransform[3],
        "extent_minx": min(minx, maxx), "extent_miny": min(miny, maxy),
        "extent_maxx": max(minx, maxx), "extent_maxy": max(miny, maxy),
        "north_up": (
            geotransform[1] > 0 and geotransform[5] < 0
            and geotransform[2] == 0 and geotransform[4] == 0
        ),
        "affine_transform": list(geotransform),
        "compression": structure.get("COMPRESSION"),
        "tiled": structure.get("TILED", "NO") == "YES"
        or (block_x < dataset.RasterXSize and block_y < dataset.RasterYSize),
        "block_x": block_x, "block_y": block_y,
        "overview_count": band.GetOverviewCount(),
        "valid_cell_count": int(valid_mask.sum()),
        "nodata_cell_count": int((~valid_mask).sum()),
        "negative_cell_count": int(np.sum(valid_values < 0)),
        "minimum": summary["min"], "maximum": summary["max"],
        "mean": summary["mean"], "std": summary["std"],
        "q01": summary["q01"], "q25": summary["q25"], "q50": summary["q50"],
        "q75": summary["q75"], "q99": summary["q99"],
        "boundary_valid_cell_count": int(boundary_valid.sum()),
        "boundary_minimum": boundary_summary["min"],
        "boundary_maximum": boundary_summary["max"],
        "boundary_mean": boundary_summary["mean"],
        "boundary_std": boundary_summary["std"],
        "boundary_negative_cell_count": int(np.sum(boundary_values < 0)),
        "boundary_quantiles": boundary_summary,
        "unique_class_count": len(class_counts) if is_landcover else None,
        "class_counts": class_counts if is_landcover else None,
        "class_area_m2": {
            code: count * resolution_x * resolution_y for code, count in class_counts.items()
        } if is_landcover else None,
        "zero_code_count": int(np.sum(array == 0)) if is_landcover else None,
        "legend_present": bool(legend_candidates) if is_landcover else None,
        "legend_candidates": [str(value) for value in legend_candidates],
        "scene_patch_pixels": [500 / resolution_x, 500 / resolution_y],
        "inspection_scope": "전체 셀",
        "elapsed_seconds": time.monotonic() - started,
        "error": None,
    }
    alignment = {
        "crs": crs["crs"],
        "resolution": [resolution_x, resolution_y],
        "origin": [geotransform[0], geotransform[3]],
        "extent": [min(minx, maxx), min(miny, maxy), max(minx, maxx), max(miny, maxy)],
        "geotransform": list(geotransform),
    }
    dataset = None
    return row, alignment


def hierarchy_audit(parquet_path: Path) -> dict[str, Any]:
    names = [f"POI_CL_DC_{i}" for i in range(1, 7)]
    table = pq.read_table(parquet_path, columns=names)
    frame = table.to_pandas(types_mapper=pd.ArrowDtype)
    levels = {}
    for name in names:
        values = frame[name].astype("string")
        levels[name] = {
            "distinct_count": int(values.nunique(dropna=True)),
            "null_count": int(values.isna().sum()),
            "null_rate": safe_rate(values.isna().sum(), len(values)),
            "empty_count": int((values == "").sum()),
        }
    parent_child = {}
    for parent, child in zip(names[:-1], names[1:]):
        pairs = frame[[parent, child]].dropna()
        ambiguity = pairs.groupby(child, observed=True)[parent].nunique()
        parent_child[f"{parent}->{child}"] = {
            "observed_path_count": int(pairs.drop_duplicates().shape[0]),
            "child_values_with_multiple_parents": int((ambiguity > 1).sum()),
            "max_parent_count_per_child": int(ambiguity.max()) if len(ambiguity) else 0,
            "rows_with_child_but_missing_parent": int(
                (frame[child].notna() & frame[parent].isna()).sum()
            ),
        }
    return {"levels": levels, "parent_child_consistency": parent_child}


def boundary_audit(boundary: gpd.GeoDataFrame, buffered: gpd.GeoDataFrame) -> dict[str, Any]:
    original = boundary.geometry.array[0]
    buffer_geom = buffered.geometry.array[0]
    original_area = float(shapely.area(original))
    buffer_area = float(shapely.area(buffer_geom))
    contains = bool(shapely.covers(buffer_geom, original))
    hausdorff = float(shapely.hausdorff_distance(shapely.boundary(buffer_geom),
                                                  shapely.boundary(original)))
    area_distance = (
        (buffer_area - original_area) / float(shapely.length(shapely.boundary(original)))
        if shapely.length(shapely.boundary(original)) else None
    )
    original_vertices = shapely.points(shapely.get_coordinates(original))
    buffer_vertices = shapely.points(shapely.get_coordinates(buffer_geom))
    original_to_buffer = shapely.distance(
        original_vertices, shapely.boundary(buffer_geom)
    )
    buffer_to_original = shapely.distance(
        buffer_vertices, shapely.boundary(original)
    )
    return {
        "boundary_area_m2": original_area,
        "buffer_area_m2": buffer_area,
        "buffer_contains_boundary": contains,
        "configured_buffer_m": (
            float(buffered["buffer_m"].iloc[0]) if "buffer_m" in buffered.columns else None
        ),
        "boundary_to_buffer_boundary_hausdorff_m": hausdorff,
        "area_difference_over_boundary_perimeter_m": area_distance,
        "boundary_vertex_to_buffer_boundary_distance_m": numeric_summary(
            np.asarray(original_to_buffer, dtype=float)
        ),
        "buffer_vertex_to_boundary_distance_m": numeric_summary(
            np.asarray(buffer_to_original, dtype=float)
        ),
        "boundary_parts": int(shapely.get_num_geometries(original)),
        "buffer_parts": int(shapely.get_num_geometries(buffer_geom)),
        "note": "Hausdorff 및 면적/둘레 비는 오목부와 코너 때문에 정확한 buffer 거리 검증값이 아님",
    }


def main() -> int:
    root, timestamp, paths_cfg, data_cfg, audit_cfg = load_configs()
    out_dir = raw_dir(root, timestamp)
    input_root = Path(paths_cfg["input_root"])
    all_names = (
        data_cfg["vector_files"] + data_cfg["tabular_files"] + data_cfg["raster_files"]
    )
    source_paths = [input_root / name for name in all_names]
    before_state = source_state(path for path in source_paths if path.exists())
    write_json(out_dir / "source_state_before.json", before_state)

    full_hash_max = int(audit_cfg["hash"]["full_hash_max_bytes"])
    sample_bytes = int(audit_cfg["hash"]["quick_fingerprint_bytes"])
    file_rows = []
    for path in source_paths:
        LOGGER.info("Inventory and hash: %s", path)
        file_rows.append(inventory_file(path, full_hash_max, sample_bytes))
    write_csv(root / "metadata" / f"{timestamp}_file_inventory.csv",
              file_rows, FILE_FIELDS)

    pseudo_tokens = set(audit_cfg["parquet"]["pseudo_missing_tokens"])
    join_names = {
        "building_id", "NF_ID", "LINK_ID", "NODE_ID", "F_NODE", "T_NODE",
        "REFRN_ID", "DEDUPE_KEY", "CLUSTER_ID", "REPRESENTATIVE_NF_ID",
    }
    layer_rows: list[dict[str, Any]] = []
    column_rows: list[dict[str, Any]] = []
    vector_summaries: list[dict[str, Any]] = []
    frames: dict[str, gpd.GeoDataFrame] = {}
    errors: list[dict[str, Any]] = []
    for filename in data_cfg["vector_files"]:
        path = input_root / filename
        try:
            for layer, _ in pyogrio.list_layers(path):
                result, columns, frame, derived = vector_layer_audit(
                    path, str(layer), pseudo_tokens, join_names
                )
                layer_rows.append(result)
                column_rows.extend(columns)
                vector_summaries.append({"file": filename, "layer": str(layer), **derived})
                frames[filename] = frame
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Vector audit failed for %s", path)
            errors.append({"stage": "vector", "source_path": str(path), "error": message})
            layer_rows.append({
                "checked_at_kst": now_kst(), "source_path": str(path),
                "layer_name": None, "inspection_scope": "실패", "error": message,
            })
    write_csv(root / "metadata" / f"{timestamp}_layer_inventory.csv",
              layer_rows, LAYER_FIELDS)

    parquet_summaries = []
    for filename in data_cfg["tabular_files"]:
        path = input_root / filename
        try:
            summary, columns = audit_parquet(path, pseudo_tokens, join_names)
            parquet_summaries.append(summary)
            column_rows.extend(columns)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Parquet audit failed for %s", path)
            errors.append({"stage": "parquet", "source_path": str(path), "error": message})
            parquet_summaries.append({
                "checked_at_kst": now_kst(), "source_path": str(path), "error": message
            })
    write_csv(root / "metadata" / f"{timestamp}_column_inventory.csv",
              column_rows, COLUMN_FIELDS)

    join_rows: list[dict[str, Any]] = []
    try:
        building_attr_path = input_root / "seoul_buildings_vworld_attributes.parquet"
        building_attr = pq.read_table(
            building_attr_path, columns=["building_id"]
        ).to_pandas()
        building_geom = frames["seoul_buildings_vworld.gpkg"]
        join_rows.append(join_audit_row(
            "building", input_root / "seoul_buildings_vworld.gpkg",
            building_attr_path, "building_id", "building_id",
            building_geom["building_id"], building_attr["building_id"],
            "동일 컬럼명", True,
        ))
    except Exception as exc:
        errors.append({"stage": "building_join", "error": f"{type(exc).__name__}: {exc}"})
    try:
        poi_attr_path = input_root / "seoul_poi_ngii_clean.parquet"
        poi_geom = frames["seoul_poi_ngii_clean.gpkg"]
        poi_id_columns = [
            name for name in pq.ParquetFile(poi_attr_path).schema_arrow.names
            if is_id_name(name)
        ]
        poi_attr = pq.read_table(poi_attr_path, columns=poi_id_columns).to_pandas()
        common_ids = [name for name in poi_id_columns if name in poi_geom.columns]
        for name in common_ids:
            join_rows.append(join_audit_row(
                "poi", input_root / "seoul_poi_ngii_clean.gpkg", poi_attr_path,
                name, name, poi_geom[name], poi_attr[name], "동일 ID형 컬럼명",
                name == "NF_ID",
            ))
    except Exception as exc:
        errors.append({"stage": "poi_join", "error": f"{type(exc).__name__}: {exc}"})
    write_csv(root / "metadata" / f"{timestamp}_join_audit.csv",
              join_rows, JOIN_FIELDS)

    raster_rows: list[dict[str, Any]] = []
    raster_alignment: dict[str, Any] = {}
    boundary_path = input_root / "seoul_boundary.gpkg"
    for filename in data_cfg["raster_files"]:
        path = input_root / filename
        try:
            LOGGER.info("Reading full raster cells: %s", path.name)
            row, alignment = audit_raster(path, boundary_path)
            raster_rows.append(row)
            raster_alignment[filename] = alignment
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Raster audit failed for %s", path)
            errors.append({"stage": "raster", "source_path": str(path), "error": message})
            raster_rows.append({
                "checked_at_kst": now_kst(), "source_path": str(path),
                "inspection_scope": "실패", "error": message,
            })
    write_csv(root / "metadata" / f"{timestamp}_raster_inventory.csv",
              raster_rows, RASTER_FIELDS)

    cross_source: dict[str, Any] = {}
    try:
        boundary_frame = frames["seoul_boundary.gpkg"]
        buffer_frame = frames["seoul_boundary_buffer400.gpkg"]
        boundary_geom = boundary_frame.geometry.array[0]
        buffer_geom = buffer_frame.geometry.array[0]
        cross_source["boundary"] = boundary_audit(boundary_frame, buffer_frame)
        coverage = {}
        for name in [
            "seoul_buildings_vworld.gpkg", "seoul_itslink.gpkg",
            "seoul_itsnode.gpkg", "seoul_poi_ngii_clean.gpkg",
        ]:
            geometry = frames[name].geometry.array
            coverage[name] = {
                "boundary": coverage_metrics(geometry, boundary_geom),
                "buffer400": coverage_metrics(geometry, buffer_geom),
            }
            for raster_name, alignment in raster_alignment.items():
                coverage[name][raster_name] = coverage_metrics(
                    geometry, bbox_polygon(alignment["extent"])
                )
        cross_source["coverage"] = coverage
        cross_source["road_node_integrity"] = endpoint_integrity(
            frames["seoul_itslink.gpkg"],
            frames["seoul_itsnode.gpkg"],
            [0.0, 0.01, 0.1, 1.0, 5.0],
        )
        raster_names = list(raster_alignment)
        if len(raster_names) == 2:
            first = raster_alignment[raster_names[0]]
            second = raster_alignment[raster_names[1]]
            cross_source["raster_alignment"] = {
                "rasters": raster_alignment,
                "same_crs": first["crs"] == second["crs"],
                "same_resolution": first["resolution"] == second["resolution"],
                "same_origin": first["origin"] == second["origin"],
                "same_extent": first["extent"] == second["extent"],
                "same_grid": (
                    first["crs"] == second["crs"]
                    and first["geotransform"] == second["geotransform"]
                    and first["extent"] == second["extent"]
                ),
            }
        extents = [
            [row["bbox_minx"], row["bbox_miny"], row["bbox_maxx"], row["bbox_maxy"]]
            for row in layer_rows if row.get("bbox_minx") is not None
        ] + [value["extent"] for value in raster_alignment.values()]
        cross_source["minimum_common_bbox"] = [
            max(extent[0] for extent in extents),
            max(extent[1] for extent in extents),
            min(extent[2] for extent in extents),
            min(extent[3] for extent in extents),
        ]
    except Exception as exc:
        LOGGER.exception("Cross-source spatial audit failed")
        errors.append({"stage": "cross_source", "error": f"{type(exc).__name__}: {exc}"})

    try:
        poi_hierarchy = hierarchy_audit(input_root / "seoul_poi_ngii_clean.parquet")
    except Exception as exc:
        poi_hierarchy = {"error": f"{type(exc).__name__}: {exc}"}
        errors.append({"stage": "poi_hierarchy", "error": poi_hierarchy["error"]})

    after_state = source_state(path for path in source_paths if path.exists())
    write_json(out_dir / "source_state_after.json", after_state)
    source_unchanged = before_state == after_state
    result = {
        "checked_at_kst": now_kst(),
        "timestamp": timestamp,
        "file_inventory": file_rows,
        "layer_inventory": layer_rows,
        "column_inventory": column_rows,
        "vector_summaries": vector_summaries,
        "parquet_summaries": parquet_summaries,
        "join_audit": join_rows,
        "raster_inventory": raster_rows,
        "cross_source": cross_source,
        "poi_hierarchy": poi_hierarchy,
        "source_state_before": before_state,
        "source_state_after": after_state,
        "source_unchanged": source_unchanged,
        "errors": errors,
        "scope_notes": {
            "geometry_quality": "전체 레코드",
            "parquet": "컬럼별 전체 스캔; 전체 행 중복은 미실행",
            "raster": "전체 셀 및 서울 경계 rasterized mask",
            "normalization": "감사 비교용 문자열 trim만 사용; 원본 미변경",
        },
    }
    write_json(out_dir / "data_audit.json", result)
    LOGGER.info("Data audit complete; source unchanged=%s; errors=%d",
                source_unchanged, len(errors))
    return 0 if source_unchanged else 2


if __name__ == "__main__":
    raise SystemExit(main())
