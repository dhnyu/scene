"""Spatial candidate queries without clipping or observation creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pyogrio
from shapely import box

from scene.miniature.exceptions import MiniatureDatasetError


@dataclass(frozen=True, slots=True)
class CandidateSource:
    entity_type: str
    source_path: Path
    source_layer: str
    source_native_id_field: str
    output_id_field: str
    geometry_types: frozenset[str]


def load_stable_id_lookup(
    path: Path,
    entity_type: str,
) -> dict[str, str]:
    """Load the M1.5 native-to-canonical ID mapping for one entity."""

    table = pq.read_table(
        path,
        columns=["source_native_id", "canonical_object_id"],
        filters=[("entity_type", "=", entity_type)],
    )
    frame = table.to_pandas()
    if frame.empty:
        raise MiniatureDatasetError(
            f"stable ID registry has no rows for {entity_type}"
        )
    if frame.isna().any().any():
        raise MiniatureDatasetError(
            f"stable ID registry has null values for {entity_type}"
        )
    if frame["source_native_id"].duplicated().any():
        raise MiniatureDatasetError(
            f"stable ID registry has duplicate native IDs for {entity_type}"
        )
    return dict(
        zip(
            frame["source_native_id"].astype(str),
            frame["canonical_object_id"].astype(str),
            strict=True,
        )
    )


def read_candidate_source(
    source: CandidateSource,
    scenes: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Read only source features whose envelopes meet the miniature bbox."""

    if not source.source_path.is_file():
        raise MiniatureDatasetError(
            f"candidate geometry source is missing: {source.source_path}"
        )
    minx, miny, maxx, maxy = (float(value) for value in scenes.total_bounds)
    frame = pyogrio.read_dataframe(
        source.source_path,
        layer=source.source_layer,
        columns=[source.source_native_id_field],
        bbox=(minx, miny, maxx, maxy),
    )
    if frame.crs is None or frame.crs.to_epsg() != 5186:
        raise MiniatureDatasetError(
            f"{source.entity_type} CRS must be EPSG:5186"
        )
    actual_types = set(frame.geometry.geom_type.dropna().astype(str))
    unexpected = sorted(actual_types - source.geometry_types)
    if unexpected:
        raise MiniatureDatasetError(
            f"{source.entity_type} has unexpected geometry types: {unexpected}"
        )
    return frame


def query_candidates(
    source_frame: gpd.GeoDataFrame,
    scenes: gpd.GeoDataFrame,
    *,
    source_native_id_field: str,
    output_id_field: str,
    stable_ids: dict[str, str],
) -> pd.DataFrame:
    """Return intersects-based candidate pairs without geometry outputs."""

    if source_native_id_field not in source_frame.columns:
        raise MiniatureDatasetError(
            f"candidate source lacks {source_native_id_field}"
        )
    scene_frame = scenes[["scene_footprint_id", "geometry"]].copy()
    joined = gpd.sjoin(
        source_frame[[source_native_id_field, "geometry"]],
        scene_frame,
        how="inner",
        predicate="intersects",
    )
    native = joined[source_native_id_field].astype("string")
    mapped = native.map(stable_ids)
    result = pd.DataFrame(
        {
            "scene_footprint_id": joined["scene_footprint_id"].astype(
                "string"
            ),
            output_id_field: mapped.astype("string"),
            "candidate_only": True,
        }
    )
    return result.sort_values(
        ["scene_footprint_id", output_id_field],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def link_raster_metadata(
    scenes: gpd.GeoDataFrame,
    raster_metadata_path: Path,
    *,
    landcover_source: str,
    dem_source: str,
) -> pd.DataFrame:
    """Link raster source references using metadata extents only."""

    columns = [
        "source_name",
        "extent_min_x",
        "extent_min_y",
        "extent_max_x",
        "extent_max_y",
        "source_reference_only",
        "pixel_data_read",
        "pixel_data_copied",
    ]
    metadata = pq.read_table(raster_metadata_path, columns=columns).to_pandas()
    indexed = metadata.set_index("source_name", drop=False)
    required = (landcover_source, dem_source)
    missing = [name for name in required if name not in indexed.index]
    if missing:
        raise MiniatureDatasetError(
            f"raster metadata lacks sources: {', '.join(missing)}"
        )
    for name in required:
        row = indexed.loc[name]
        if isinstance(row, pd.DataFrame):
            raise MiniatureDatasetError(
                f"raster metadata duplicates source {name}"
            )
        if (
            not bool(row["source_reference_only"])
            or bool(row["pixel_data_read"])
            or bool(row["pixel_data_copied"])
        ):
            raise MiniatureDatasetError(
                f"raster metadata violates reference-only policy: {name}"
            )
        extent = box(
            float(row["extent_min_x"]),
            float(row["extent_min_y"]),
            float(row["extent_max_x"]),
            float(row["extent_max_y"]),
        )
        if not scenes.geometry.intersects(extent).all():
            raise MiniatureDatasetError(
                f"raster extent does not intersect every selected scene: {name}"
            )
    return pd.DataFrame(
        {
            "scene_footprint_id": scenes["scene_footprint_id"]
            .astype("string")
            .to_list(),
            "landcover_source": [landcover_source] * len(scenes),
            "dem_source": [dem_source] * len(scenes),
        }
    ).sort_values("scene_footprint_id", kind="stable").reset_index(drop=True)
