"""Read-only district balancing statistics from canonical adapter outputs."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
import pandas as pd
import pyarrow.parquet as pq
import pyogrio
import shapely
from shapely import box

from scene.boundaries.provenance import district_content_hash
from scene.core.config import DistrictAssignmentConfig, ProjectConfig
from scene.inventory.hashing import sha256_file
from scene.split.exceptions import DistrictAssignmentError
from scene.split.provenance import BalancingStatistics, CanonicalDistrictInput


gdal.UseExceptions()


def _canonical_json_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_canonical_districts(
    config: DistrictAssignmentConfig,
) -> CanonicalDistrictInput:
    """Load only the M1.5.1 canonical layer and verify its frozen content."""

    path = config.canonical_boundary_path
    if not path.is_file():
        raise DistrictAssignmentError(
            f"canonical district GeoPackage is missing: {path}"
        )
    layers = {str(item[0]) for item in pyogrio.list_layers(path)}
    if config.canonical_boundary_layer not in layers:
        raise DistrictAssignmentError(
            "canonical district layer is missing: "
            f"{config.canonical_boundary_layer}"
        )
    districts = pyogrio.read_dataframe(
        path,
        layer=config.canonical_boundary_layer,
    ).sort_values("district_code", ignore_index=True)
    required = {
        "district_id",
        "district_code",
        "district_name",
        "source_name",
        "source_object_id",
        "source_sha256",
        "geometry",
    }
    missing = sorted(required - set(districts.columns))
    if missing:
        raise DistrictAssignmentError(
            f"canonical district fields are missing: {missing}"
        )
    if len(districts) != 25:
        raise DistrictAssignmentError(
            f"canonical district count is {len(districts)}, expected 25"
        )
    if districts.crs is None or districts.crs.to_epsg() != 5186:
        raise DistrictAssignmentError(
            f"canonical district CRS is {districts.crs}, expected EPSG:5186"
        )
    if (
        districts["district_id"].isna().any()
        or districts["district_id"].duplicated().any()
        or districts["district_code"].isna().any()
        or districts["district_code"].duplicated().any()
        or districts.geometry.isna().any()
        or districts.geometry.is_empty.any()
        or (~districts.geometry.is_valid).any()
    ):
        raise DistrictAssignmentError(
            "canonical district IDs, codes, or geometry are invalid"
        )
    content_hash = district_content_hash(districts)
    if content_hash != config.canonical_boundary_content_hash:
        raise DistrictAssignmentError(
            "canonical district content hash mismatch: "
            f"{content_hash} != {config.canonical_boundary_content_hash}"
        )
    return CanonicalDistrictInput(
        districts=districts,
        geopackage_path=path,
        layer=config.canonical_boundary_layer,
        geopackage_sha256=sha256_file(path),
        content_hash=content_hash,
    )


def _point_counts(
    geometry_path: Path,
    layer: str,
    districts: gpd.GeoDataFrame,
    *,
    representative_points: bool,
    id_column: str,
) -> tuple[pd.Series, pd.DataFrame, int]:
    objects = pyogrio.read_dataframe(
        geometry_path,
        layer=layer,
        columns=[id_column],
    )
    points = (
        objects.geometry.representative_point()
        if representative_points
        else objects.geometry
    )
    point_frame = gpd.GeoDataFrame(
        {id_column: objects[id_column].astype("string")},
        geometry=points,
        crs=objects.crs,
    )
    joined = gpd.sjoin(
        point_frame,
        districts[["district_code", "geometry"]],
        predicate="within",
        how="left",
    )
    counts = joined["district_code"].value_counts()
    unmatched = int(joined["district_code"].isna().sum())
    return counts, joined[[id_column, "district_code"]], unmatched


def _road_lengths(
    path: Path,
    layer: str,
    districts: gpd.GeoDataFrame,
) -> tuple[pd.Series, int]:
    roads = pyogrio.read_dataframe(
        path,
        layer=layer,
        columns=["source_link_id"],
    )
    candidates = gpd.sjoin(
        roads,
        districts[["district_code", "geometry"]],
        predicate="intersects",
        how="left",
    )
    unmatched = int(candidates["district_code"].isna().sum())
    matched = candidates.loc[candidates["district_code"].notna()].copy()
    district_geometries = gpd.GeoSeries(
        districts.geometry.iloc[
            matched["index_right"].astype(int).to_numpy()
        ].array,
        index=matched.index,
        crs=districts.crs,
    )
    lengths = matched.geometry.intersection(
        district_geometries,
        align=False,
    ).length
    result = lengths.groupby(matched["district_code"]).sum()
    return result, unmatched


def _eligible_window_count(
    geometry: object,
    *,
    side_length_m: float,
    stride_m: float,
    origin: tuple[float, float],
) -> int:
    half = side_length_m / 2.0
    min_x, min_y, max_x, max_y = geometry.bounds
    first_x = math.ceil((min_x + half - origin[0]) / stride_m)
    last_x = math.floor((max_x - half - origin[0]) / stride_m)
    first_y = math.ceil((min_y + half - origin[1]) / stride_m)
    last_y = math.floor((max_y - half - origin[1]) / stride_m)
    if first_x > last_x or first_y > last_y:
        return 0
    x = origin[0] + np.arange(first_x, last_x + 1) * stride_m
    y = origin[1] + np.arange(first_y, last_y + 1) * stride_m
    xx, yy = np.meshgrid(x, y)
    windows = box(
        xx.ravel() - half,
        yy.ravel() - half,
        xx.ravel() + half,
        yy.ravel() + half,
    )
    return int(np.count_nonzero(shapely.covers(geometry, windows)))


def _raster_epsg(dataset: gdal.Dataset) -> int | None:
    reference = osr.SpatialReference()
    reference.ImportFromWkt(dataset.GetProjection())
    code = reference.GetAuthorityCode(None)
    return int(code) if code is not None else None


def _district_raster_values(
    dataset: gdal.Dataset,
    geometry: object,
) -> tuple[np.ndarray, int]:
    transform = dataset.GetGeoTransform()
    if transform[2] != 0.0 or transform[4] != 0.0:
        raise DistrictAssignmentError(
            "rotated raster grids are unsupported for M1.6 statistics"
        )
    min_x, min_y, max_x, max_y = geometry.bounds
    pixel_width = transform[1]
    pixel_height = abs(transform[5])
    x_start = max(
        0,
        int(math.floor((min_x - transform[0]) / pixel_width)),
    )
    x_stop = min(
        dataset.RasterXSize,
        int(math.ceil((max_x - transform[0]) / pixel_width)),
    )
    y_start = max(
        0,
        int(math.floor((transform[3] - max_y) / pixel_height)),
    )
    y_stop = min(
        dataset.RasterYSize,
        int(math.ceil((transform[3] - min_y) / pixel_height)),
    )
    array = dataset.GetRasterBand(1).ReadAsArray(
        x_start,
        y_start,
        x_stop - x_start,
        y_stop - y_start,
    )
    x = transform[0] + (np.arange(x_start, x_stop) + 0.5) * pixel_width
    y = transform[3] - (np.arange(y_start, y_stop) + 0.5) * pixel_height
    inside = shapely.contains_xy(geometry, x[None, :], y[:, None])
    values = np.asarray(array)[inside]
    finite = np.isfinite(values)
    nodata = dataset.GetRasterBand(1).GetNoDataValue()
    valid = finite if nodata is None else finite & (values != nodata)
    return values[valid], int(values.size - np.count_nonzero(valid))


def _raster_statistics(
    districts: gpd.GeoDataFrame,
    path: Path,
    *,
    categorical: bool,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise DistrictAssignmentError(f"cannot open raster: {path}")
    if _raster_epsg(dataset) != 5186 or dataset.RasterCount != 1:
        raise DistrictAssignmentError(
            f"raster is not one-band EPSG:5186: {path}"
        )
    records: list[dict[str, object]] = []
    for row in districts.itertuples():
        values, nodata_count = _district_raster_values(
            dataset,
            row.geometry,
        )
        if values.size == 0:
            raise DistrictAssignmentError(
                f"district has no valid raster cells: {row.district_code}"
            )
        if categorical:
            codes, counts = np.unique(values, return_counts=True)
            distribution = {
                str(int(code) if float(code).is_integer() else float(code)): int(count)
                for code, count in zip(codes, counts, strict=True)
            }
            records.append(
                {
                    "district_code": str(row.district_code),
                    "landcover_cell_count": int(values.size),
                    "landcover_nodata_count": nodata_count,
                    "landcover_raw_code_counts": distribution,
                }
            )
        else:
            quantiles = np.quantile(values.astype(np.float64), [0.25, 0.5, 0.75])
            records.append(
                {
                    "district_code": str(row.district_code),
                    "dem_valid_cell_count": int(values.size),
                    "dem_nodata_count": nodata_count,
                    "dem_min_raw": float(np.min(values)),
                    "dem_q25_raw": float(quantiles[0]),
                    "dem_q50_raw": float(quantiles[1]),
                    "dem_q75_raw": float(quantiles[2]),
                    "dem_max_raw": float(np.max(values)),
                    "dem_mean_raw": float(np.mean(values, dtype=np.float64)),
                    "dem_std_raw": float(np.std(values, dtype=np.float64)),
                }
            )
    provenance = {
        "path": str(path),
        "sha256": sha256_file(path),
        "file_size": path.stat().st_size,
        "modified_time_ns": path.stat().st_mtime_ns,
        "pixel_assignment": "source-grid pixel center within district",
        "values_modified": False,
    }
    if categorical:
        provenance["semantic_label_status"] = "D-008 Open; raw codes only"
    else:
        provenance["unit_status"] = "D-009 Open; unresolved"
    return records, provenance


def _source_by_name(config: ProjectConfig, name: str) -> Path:
    for source in config.sources:
        if source.source_name == name:
            return source.path
    raise DistrictAssignmentError(f"registered source is missing: {name}")


def compute_balancing_statistics(
    project: ProjectConfig,
    canonical: CanonicalDistrictInput,
) -> BalancingStatistics:
    """Compute every D-005 balance diagnostic without changing source data."""

    config = project.district_assignment
    if config is None:
        raise DistrictAssignmentError(
            "district_assignment configuration is required"
        )
    districts = canonical.districts
    area_m2 = districts.geometry.area.astype(float)
    area_km2 = area_m2 / 1_000_000.0

    building_counts, _, building_unmatched = _point_counts(
        config.building_geometry_path,
        config.building_geometry_layer,
        districts,
        representative_points=True,
        id_column="source_building_id",
    )
    road_lengths, road_unmatched = _road_lengths(
        config.road_geometry_path,
        config.road_geometry_layer,
        districts,
    )
    poi_counts, poi_join, poi_unmatched = _point_counts(
        config.poi_geometry_path,
        config.poi_geometry_layer,
        districts,
        representative_points=False,
        id_column="source_poi_id",
    )
    poi_attributes = pq.read_table(
        config.poi_attributes_path,
        columns=["source_poi_id", "poi_category_1"],
    ).to_pandas()
    poi_categories = poi_join.merge(
        poi_attributes,
        on="source_poi_id",
        how="left",
        validate="one_to_one",
    )
    category_counts: dict[str, dict[str, int]] = {}
    for district_code, group in poi_categories.dropna(
        subset=["district_code"]
    ).groupby("district_code"):
        valid = group["poi_category_1"].dropna()
        category_counts[str(district_code)] = {
            str(label): int(count)
            for label, count in valid.value_counts().sort_index().items()
        }

    landcover_path = _source_by_name(project, config.landcover_source_name)
    dem_path = _source_by_name(project, config.dem_source_name)
    landcover_records, landcover_provenance = _raster_statistics(
        districts,
        landcover_path,
        categorical=True,
    )
    dem_records, dem_provenance = _raster_statistics(
        districts,
        dem_path,
        categorical=False,
    )
    landcover = {
        str(record["district_code"]): record
        for record in landcover_records
    }
    dem = {str(record["district_code"]): record for record in dem_records}

    rows: list[dict[str, Any]] = []
    for index, district in districts.iterrows():
        code = str(district["district_code"])
        building_count = int(building_counts.get(code, 0))
        road_length_m = float(road_lengths.get(code, 0.0))
        poi_count = int(poi_counts.get(code, 0))
        lc = landcover[code]
        elevation = dem[code]
        rows.append(
            {
                "district_id": str(district["district_id"]),
                "district_code": code,
                "district_name": str(district["district_name"]),
                "area_m2": float(area_m2.iloc[index]),
                "area_km2": float(area_km2.iloc[index]),
                "eligible_scene_estimate": _eligible_window_count(
                    district.geometry,
                    side_length_m=config.scene_side_length_m,
                    stride_m=config.scene_stride_m,
                    origin=config.grid_origin_m,
                ),
                "building_count": building_count,
                "building_density_per_km2": (
                    building_count / float(area_km2.iloc[index])
                ),
                "road_length_m": road_length_m,
                "road_length_km": road_length_m / 1000.0,
                "road_density_km_per_km2": (
                    (road_length_m / 1000.0) / float(area_km2.iloc[index])
                ),
                "poi_count": poi_count,
                "poi_density_per_km2": (
                    poi_count / float(area_km2.iloc[index])
                ),
                "poi_category_1_counts": category_counts.get(code, {}),
                **{key: value for key, value in lc.items() if key != "district_code"},
                **{
                    key: value
                    for key, value in elevation.items()
                    if key != "district_code"
                },
                "centroid_x_m": float(district.geometry.centroid.x),
                "centroid_y_m": float(district.geometry.centroid.y),
            }
        )
    frame = pd.DataFrame(rows).sort_values(
        "district_code",
        ignore_index=True,
    )
    landcover_codes = tuple(
        sorted(
            {
                code
                for counts in frame["landcover_raw_code_counts"]
                for code in counts
            }
        )
    )
    categories = tuple(
        sorted(
            {
                category
                for counts in frame["poi_category_1_counts"]
                for category in counts
            }
        )
    )
    source_provenance = {
        "canonical_districts": {
            "path": str(canonical.geopackage_path),
            "layer": canonical.layer,
            "sha256": canonical.geopackage_sha256,
            "content_hash": canonical.content_hash,
        },
        "buildings": {
            "path": str(config.building_geometry_path),
            "layer": config.building_geometry_layer,
            "sha256": sha256_file(config.building_geometry_path),
            "unmatched_feature_count": building_unmatched,
            "assignment_method": "representative point within district",
        },
        "roads": {
            "path": str(config.road_geometry_path),
            "layer": config.road_geometry_layer,
            "sha256": sha256_file(config.road_geometry_path),
            "unmatched_feature_count": road_unmatched,
            "assignment_method": "geometry intersection length by district",
        },
        "pois": {
            "path": str(config.poi_geometry_path),
            "layer": config.poi_geometry_layer,
            "sha256": sha256_file(config.poi_geometry_path),
            "attribute_path": str(config.poi_attributes_path),
            "attribute_sha256": sha256_file(config.poi_attributes_path),
            "unmatched_feature_count": poi_unmatched,
            "assignment_method": "point within district",
            "category_level": "poi_category_1 raw canonical label",
        },
        "landcover": landcover_provenance,
        "dem": dem_provenance,
    }
    method = {
        "eligible_scene_estimate": (
            "count only; 500m square fully covered by one district on the "
            "250m origin-(0,0) grid; no footprint artifact materialized"
        ),
        "density_denominator": "canonical district area in square kilometres",
        "landcover": "raw source-grid code counts; no semantic mapping",
        "dem": "raw valid-cell distribution; unit unresolved",
        "urban_context": (
            "derived later from numeric density, raster, elevation, and "
            "centroid diagnostics"
        ),
    }
    hash_records = json.loads(
        frame.to_json(orient="records", force_ascii=False, double_precision=15)
    )
    statistics_hash = _canonical_json_hash(
        {
            "districts": hash_records,
            "landcover_codes": landcover_codes,
            "method": method,
            "poi_categories": categories,
            "source_provenance": source_provenance,
        }
    )
    return BalancingStatistics(
        frame=frame,
        landcover_codes=landcover_codes,
        poi_categories=categories,
        source_provenance=source_provenance,
        method=method,
        statistics_hash=statistics_hash,
    )
