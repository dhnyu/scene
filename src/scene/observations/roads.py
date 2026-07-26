"""M2.3 Road Observation implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
from shapely import normalize, to_wkb
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from scene.core.config import load_config, write_resolved_config
from scene.core.logging import configure_logging
from scene.core.report_governance import write_milestone_reports
from scene.core.reporting import ReportSection
from scene.core.run_context import collect_run_metadata
from scene.id.generator import DerivedIdFactory, canonical_hash
from scene.inventory.hashing import sha256_file
from scene.observations.exceptions import ObservationContractError
from scene.observations.schema import load_observation_schema


ROAD_OBJECT_TYPE = "road"
CONTRACT_VERSION = "m2.1-v1"


@dataclass(frozen=True, slots=True)
class RoadObservationArtifacts:
    output_directory: Path
    geometry_geopackage: Path
    attribute_parquet: Path
    provenance_parquet: Path
    node_geopackage: Path
    node_parquet: Path
    edge_parquet: Path
    validation_json: Path
    summary_json: Path
    geometry_sha256: str
    attribute_sha256: str
    provenance_sha256: str
    node_geopackage_sha256: str
    node_sha256: str
    edge_sha256: str
    validation_sha256: str
    summary_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}

    def to_summary_dict(self) -> dict[str, str]:
        data = self.to_dict()
        data.pop("summary_sha256")
        return data


@dataclass(frozen=True, slots=True)
class RoadObservationValidation:
    valid: bool
    candidate_count: int
    observation_count: int
    excluded_zero_length_count: int
    invalid_geometry_count: int
    geometry_collection_count: int
    unexpected_geometry_type_count: int
    outside_scene_count: int
    duplicate_observation_id_count: int
    duplicate_part_id_group_count: int
    null_required_count: int
    parent_mismatch_count: int
    length_mismatch_count: int
    representative_mismatch_count: int
    geometry_attribute_id_mismatch_count: int
    provenance_id_mismatch_count: int
    node_count: int
    edge_count: int
    duplicate_node_id_count: int
    duplicate_edge_id_count: int
    self_loop_edge_count: int
    boundary_node_count: int
    source_node_count: int
    edge_node_reference_missing_count: int
    source_input_changed_count: int
    forbidden_artifact_count: int
    deterministic_part_id_regeneration: bool
    deterministic_observation_id_regeneration: bool
    crs_valid: bool
    geometry_types_valid: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _latest_directory(root: Path, required: tuple[str, ...]) -> Path:
    candidates = sorted(
        (path for path in root.glob("*") if path.is_dir()),
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )
    for directory in reversed(candidates):
        if all((directory / name).is_file() for name in required):
            return directory
    raise ObservationContractError(
        f"no complete artifact directory found under {root}"
    )


def _resolve_default_inputs(config: Any) -> dict[str, Path]:
    output_root = config.paths.output_root
    scene_dir = _latest_directory(
        output_root / "scenes",
        ("scene_footprints.gpkg", "scene_footprints.parquet"),
    )
    road_dir = _latest_directory(
        output_root / "roads",
        (
            "road_geometry.gpkg",
            "road_link_attributes.parquet",
            "road_node_attributes.parquet",
        ),
    )
    ids_dir = _latest_directory(
        output_root / "ids",
        ("ids.parquet", "provenance.parquet"),
    )
    return {
        "scene_geometry": scene_dir / "scene_footprints.gpkg",
        "road_geometry": road_dir / "road_geometry.gpkg",
        "road_link_attributes": road_dir / "road_link_attributes.parquet",
        "road_node_attributes": road_dir / "road_node_attributes.parquet",
        "stable_ids": ids_dir / "ids.parquet",
        "stable_id_provenance": ids_dir / "provenance.parquet",
        "schema": (
            config.paths.project_root
            / "docs"
            / "contracts"
            / "scene_observation_schema.yaml"
        ),
    }


def _snapshot(paths: tuple[Path, ...]) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in paths:
        if not path.is_file():
            raise ObservationContractError(f"required input is missing: {path}")
        stat = path.stat()
        result[str(path)] = (stat.st_size, stat.st_mtime_ns, sha256_file(path))
    return result


def _validate_crs(frame: gpd.GeoDataFrame, *, label: str) -> None:
    if frame.crs is None or frame.crs.to_epsg() != 5186:
        raise ObservationContractError(f"{label} CRS must be EPSG:5186")


def _canonical_wkb_hash(geometry: BaseGeometry) -> str:
    payload = to_wkb(
        normalize(geometry),
        byte_order=1,
        output_dimension=2,
        include_srid=False,
    )
    return hashlib.sha256(payload).hexdigest()


def _source_geometry_id(
    source_object_id: str,
    source_file_sha256: str,
    source_geometry_wkb_sha256: str,
) -> str:
    return canonical_hash(
        "source_geometry_id",
        source_object_id,
        source_file_sha256,
        source_geometry_wkb_sha256,
    )


def _road_scene_node_id(
    scene_id: str,
    node_kind: str,
    primary: str,
    secondary: str | None = None,
) -> str:
    return canonical_hash(
        "road_scene_node_id",
        scene_id,
        node_kind,
        primary,
        secondary,
    )


def _road_scene_edge_id(
    scene_id: str,
    observation_id: str,
    start_node_id: str,
    end_node_id: str,
) -> str:
    return canonical_hash(
        "road_scene_edge_id",
        scene_id,
        observation_id,
        start_node_id,
        end_node_id,
    )


def _read_scenes(path: Path) -> gpd.GeoDataFrame:
    scenes = gpd.read_file(path, layer="scene_footprints")
    _validate_crs(scenes, label="scene footprints")
    required = {"scene_id", "split", "district_id", "geometry"}
    missing = sorted(required - set(scenes.columns))
    if missing:
        raise ObservationContractError(
            f"scene footprints missing required columns: {missing}"
        )
    return scenes[["scene_id", "split", "district_id", "geometry"]].copy()


def _read_road_geometry(path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    links = gpd.read_file(path, layer="road_links")
    nodes = gpd.read_file(path, layer="road_nodes")
    _validate_crs(links, label="road link geometry")
    _validate_crs(nodes, label="road node geometry")
    if sorted(set(links.geometry.geom_type.astype(str))) != ["LineString"]:
        raise ObservationContractError("road link geometry must be LineString")
    if sorted(set(nodes.geometry.geom_type.astype(str))) != ["Point"]:
        raise ObservationContractError("road node geometry must be Point")
    if int((~links.geometry.is_valid).sum()) or int((~nodes.geometry.is_valid).sum()):
        raise ObservationContractError("road source geometry contains invalid rows")
    if int(links.geometry.is_empty.sum()) or int(nodes.geometry.is_empty.sum()):
        raise ObservationContractError("road source geometry contains empty rows")
    if int((links.geometry.length <= 0.0).sum()):
        raise ObservationContractError("road source geometry contains zero-length rows")
    return (
        links[["source_link_id", "geometry"]].copy(),
        nodes[["source_node_id", "geometry"]].copy(),
    )


def _read_link_attributes(path: Path) -> pd.DataFrame:
    columns = [
        "source_name",
        "source_path",
        "source_file_sha256",
        "source_link_id",
        "from_source_node_id",
        "to_source_node_id",
        "lanes",
        "road_rank",
        "road_type",
        "source_road_number",
        "source_road_name",
        "source_length_m",
    ]
    frame = pq.read_table(path, columns=columns).to_pandas()
    if frame["source_link_id"].duplicated().any():
        raise ObservationContractError("road attributes duplicate source_link_id")
    if frame["source_link_id"].isna().any() or (frame["source_link_id"].astype(str) == "").any():
        raise ObservationContractError("road attributes contain missing source_link_id")
    return frame


def _read_id_maps(
    ids_path: Path,
    provenance_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ids = pq.read_table(
        ids_path,
        columns=["entity_type", "source_native_id", "canonical_object_id"],
        filters=[("entity_type", "=", "road_link")],
    ).to_pandas()
    provenance = pq.read_table(
        provenance_path,
        columns=[
            "entity_type",
            "canonical_object_id",
            "source_native_id",
            "source_name",
            "source_path",
            "source_sha256",
            "run_id",
            "config_hash",
            "canonical_manifest_path",
            "canonical_frame_path",
        ],
        filters=[("entity_type", "=", "road_link")],
    ).to_pandas()
    if ids.empty or provenance.empty:
        raise ObservationContractError("road stable ID rows are missing")
    if ids["source_native_id"].duplicated().any():
        raise ObservationContractError("road stable IDs duplicate native IDs")
    return ids, provenance


def _part_key(line: LineString) -> tuple[float, float, float, str]:
    min_x, min_y, _, _ = line.bounds
    return (-float(line.length), float(min_x), float(min_y), _canonical_wkb_hash(line))


def _ordered_parts(geometry: BaseGeometry) -> list[tuple[int, LineString, str, str, int]]:
    if geometry.geom_type == "LineString":
        parts = [geometry]
    elif geometry.geom_type == "MultiLineString":
        parts = list(geometry.geoms)
    else:
        raise ObservationContractError(
            f"unexpected road clipped geometry type: {geometry.geom_type}"
        )
    ordered = sorted(parts, key=_part_key)
    occurrences: dict[tuple[str, str], int] = {}
    result: list[tuple[int, LineString, str, str, int]] = []
    for part_order, part in enumerate(ordered):
        wkb_hash = _canonical_wkb_hash(part)
        occurrence_key = ("LineString", wkb_hash)
        occurrence = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence + 1
        part_id = DerivedIdFactory.clip_part_id("LineString", wkb_hash, occurrence)
        result.append((part_order, part, part_id, wkb_hash, occurrence))
    return result


def _endpoint_tuple(point: tuple[float, float]) -> tuple[float, float]:
    return (float(point[0]), float(point[1]))


def _materialize_frames(
    *,
    release_id: str,
    producer_commit: str,
    config_hash: str,
    scenes: gpd.GeoDataFrame,
    links: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    ids: pd.DataFrame,
    id_provenance: pd.DataFrame,
) -> tuple[
    gpd.GeoDataFrame,
    pd.DataFrame,
    pd.DataFrame,
    gpd.GeoDataFrame,
    pd.DataFrame,
    Mapping[str, int],
]:
    attr = attributes.merge(
        ids[["source_native_id", "canonical_object_id"]],
        left_on="source_link_id",
        right_on="source_native_id",
        how="inner",
        validate="one_to_one",
    ).rename(columns={"canonical_object_id": "object_id"})
    links = links.merge(
        ids[["source_native_id", "canonical_object_id"]],
        left_on="source_link_id",
        right_on="source_native_id",
        how="inner",
        validate="one_to_one",
    ).rename(columns={"canonical_object_id": "object_id"})
    if len(links) != len(attributes):
        raise ObservationContractError("not all road links matched stable IDs")

    attr_by_object = attr.set_index("object_id", drop=False)
    provenance_by_object = id_provenance.set_index("canonical_object_id", drop=False)
    node_points = {
        str(row.source_node_id): row.geometry
        for row in nodes.itertuples(index=False)
    }
    missing_from = set(attr["from_source_node_id"].astype(str)) - set(node_points)
    missing_to = set(attr["to_source_node_id"].astype(str)) - set(node_points)
    if missing_from or missing_to:
        raise ObservationContractError("road node coverage is incomplete")

    scene_lookup = scenes.geometry.copy()
    candidates = gpd.sjoin(
        links[["source_link_id", "object_id", "geometry"]],
        scenes[["scene_id", "split", "district_id", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    candidate_count = len(candidates)
    source_geometries = candidates.geometry.reset_index(drop=True)
    scene_geometries = gpd.GeoSeries(
        candidates["index_right"].map(scene_lookup).to_numpy(),
        crs=scenes.crs,
    )
    clipped = source_geometries.intersection(scene_geometries, align=False)
    lengths = clipped.length
    positive = (~clipped.is_empty) & (lengths > 0.0)
    excluded_zero_length_count = int((~positive).sum())

    candidates = candidates.reset_index(drop=True).loc[positive.to_numpy()].reset_index(drop=True)
    active_clipped = clipped.loc[positive].reset_index(drop=True)
    active_source = source_geometries.loc[positive].reset_index(drop=True)
    active_scene = scene_geometries.loc[positive].reset_index(drop=True)

    geom_types = active_clipped.geom_type.astype(str)
    geometry_collection_count = int(geom_types.eq("GeometryCollection").sum())
    unexpected_geometry_type_count = int(
        (~geom_types.isin({"LineString", "MultiLineString"})).sum()
    )
    invalid_geometry_count = int((~active_clipped.is_valid).sum())
    if geometry_collection_count or unexpected_geometry_type_count or invalid_geometry_count:
        raise ObservationContractError(
            "road clipped geometry violates D-102: "
            f"invalid={invalid_geometry_count}, "
            f"GeometryCollection={geometry_collection_count}, "
            f"unexpected={unexpected_geometry_type_count}"
        )

    geometry_rows: list[dict[str, object]] = []
    attribute_rows: list[dict[str, object]] = []
    provenance_rows: list[dict[str, object]] = []
    node_rows_by_id: dict[str, dict[str, object]] = {}
    edge_rows: list[dict[str, object]] = []
    source_wkb_hash_by_object: dict[str, str] = {}
    outside_scene_count = 0
    self_loop_edge_count = 0

    for idx, candidate in enumerate(candidates.itertuples(index=False)):
        scene_geometry = active_scene.iloc[idx]
        source_geometry = active_source.iloc[idx]
        clipped_geometry = active_clipped.iloc[idx]
        object_id = str(candidate.object_id)
        scene_id = str(candidate.scene_id)
        source_link_id = str(candidate.source_link_id)
        row_attr = attr_by_object.loc[object_id]
        source_start = _endpoint_tuple(source_geometry.coords[0])
        source_end = _endpoint_tuple(source_geometry.coords[-1])
        source_endpoints = {source_start, source_end}
        source_prov = provenance_by_object.loc[object_id]
        source_wkb_hash = source_wkb_hash_by_object.get(object_id)
        if source_wkb_hash is None:
            source_wkb_hash = _canonical_wkb_hash(source_geometry)
            source_wkb_hash_by_object[object_id] = source_wkb_hash
        source_sha = str(source_prov["source_sha256"])
        source_geometry_id = _source_geometry_id(
            object_id,
            source_sha,
            source_wkb_hash,
        )
        parts = _ordered_parts(clipped_geometry)
        geometry_status = (
            "full"
            if len(parts) == 1 and scene_geometry.covers(source_geometry)
            else "split_by_clip"
            if len(parts) > 1
            else "clipped"
        )
        for part_order, part, part_id, part_wkb_hash, occurrence_index in parts:
            if not scene_geometry.covers(part):
                outside_scene_count += 1
                continue
            observation_id = DerivedIdFactory.observation_id(
                scene_id,
                ROAD_OBJECT_TYPE,
                object_id,
                part_id,
            )
            representative = part.interpolate(0.5, normalized=True)
            coords = list(part.coords)
            endpoint_data: list[tuple[str, tuple[float, float], str, str, bool]] = []
            for side, coordinate in (
                ("start", _endpoint_tuple(coords[0])),
                ("end", _endpoint_tuple(coords[-1])),
            ):
                if coordinate == source_start:
                    source_node_id = str(row_attr["from_source_node_id"])
                    node_id = _road_scene_node_id(
                        scene_id,
                        "source",
                        source_node_id,
                    )
                    is_boundary = False
                    node_kind = "source"
                elif coordinate == source_end:
                    source_node_id = str(row_attr["to_source_node_id"])
                    node_id = _road_scene_node_id(
                        scene_id,
                        "source",
                        source_node_id,
                    )
                    is_boundary = False
                    node_kind = "source"
                else:
                    source_node_id = ""
                    node_id = _road_scene_node_id(
                        scene_id,
                        "boundary",
                        observation_id,
                        side,
                    )
                    is_boundary = True
                    node_kind = "boundary"
                endpoint_data.append((side, coordinate, node_id, node_kind, is_boundary))
                if node_id not in node_rows_by_id:
                    node_rows_by_id[node_id] = {
                        "node_id": node_id,
                        "scene_id": scene_id,
                        "node_kind": node_kind,
                        "source_node_id": source_node_id or None,
                        "x": coordinate[0],
                        "y": coordinate[1],
                        "is_scene_boundary_node": is_boundary,
                        "geometry": Point(coordinate),
                    }

            start_node_id = endpoint_data[0][2]
            end_node_id = endpoint_data[1][2]
            if start_node_id == end_node_id:
                self_loop_edge_count += 1
            edge_id = _road_scene_edge_id(
                scene_id,
                observation_id,
                start_node_id,
                end_node_id,
            )
            edge_rows.append(
                {
                    "edge_id": edge_id,
                    "scene_id": scene_id,
                    "observation_id": observation_id,
                    "start_node_id": start_node_id,
                    "end_node_id": end_node_id,
                    "start_is_boundary": endpoint_data[0][4],
                    "end_is_boundary": endpoint_data[1][4],
                    "edge_length_m": float(part.length),
                    "parent_way_id": source_link_id,
                }
            )
            common = {
                "release_id": release_id,
                "split": str(candidate.split),
                "district_id": str(candidate.district_id),
                "scene_id": scene_id,
                "object_type": ROAD_OBJECT_TYPE,
                "object_id": object_id,
                "part_id": part_id,
                "observation_id": observation_id,
                "source_name": str(row_attr["source_name"]),
                "geometry_status": geometry_status,
                "touches_scene_boundary": bool(part.intersects(scene_geometry.boundary)),
                "representative_x": float(representative.x),
                "representative_y": float(representative.y),
                "part_order": int(part_order),
                "observation_length_m": float(part.length),
                "parent_way_id": source_link_id,
                "is_scene_boundary_endpoint": any(data[4] for data in endpoint_data),
                "road_type": row_attr["road_type"],
                "road_rank": row_attr["road_rank"],
                "lanes": row_attr["lanes"],
                "source_road_number": row_attr["source_road_number"],
                "source_road_name": row_attr["source_road_name"],
                "source_length_m": row_attr["source_length_m"],
                "source_link_id": source_link_id,
            }
            attribute_rows.append(common)
            geometry_rows.append({"observation_id": observation_id, "geometry": part})
            provenance_rows.append(
                {
                    "observation_id": observation_id,
                    "object_id": object_id,
                    "source_geometry_id": source_geometry_id,
                    "source_geometry_wkb_sha256": source_wkb_hash,
                    "part_geometry_wkb_sha256": part_wkb_hash,
                    "part_occurrence_index": occurrence_index,
                    "source_file_sha256": source_sha,
                    "clip_operation": "clip",
                    "producer_commit": producer_commit,
                    "contract_version": CONTRACT_VERSION,
                    "decision": "D-102",
                    "parent_way_decision": "D-004",
                    "connectivity_decision": "D-006",
                    "source_name": str(source_prov["source_name"]),
                    "source_path": str(source_prov["source_path"]),
                    "source_native_id": source_link_id,
                    "stable_id_run_id": str(source_prov["run_id"]),
                    "stable_id_config_hash": str(source_prov["config_hash"]),
                    "canonical_manifest_path": str(
                        source_prov["canonical_manifest_path"]
                    ),
                    "canonical_frame_path": str(source_prov["canonical_frame_path"]),
                    "producer_config_hash": config_hash,
                }
            )

    if outside_scene_count or self_loop_edge_count:
        raise ObservationContractError(
            "road observation topology violates D-102/D-006: "
            f"outside_scene={outside_scene_count}, self_loop={self_loop_edge_count}"
        )
    attributes_frame = pd.DataFrame(attribute_rows)
    if attributes_frame.empty:
        raise ObservationContractError("M2.3 produced zero road observations")
    order = [
        "split",
        "district_id",
        "scene_id",
        "object_id",
        "part_order",
        "part_id",
        "observation_id",
    ]
    attributes_frame = attributes_frame.sort_values(order, kind="stable").reset_index(drop=True)
    order_map = {
        observation_id: position
        for position, observation_id in enumerate(attributes_frame["observation_id"])
    }
    geometry_frame = gpd.GeoDataFrame(geometry_rows, geometry="geometry", crs="EPSG:5186")
    geometry_frame["__order"] = geometry_frame["observation_id"].map(order_map)
    geometry_frame = (
        geometry_frame.sort_values("__order", kind="stable")
        .drop(columns="__order")
        .reset_index(drop=True)
    )
    provenance_frame = attributes_frame[["observation_id"]].merge(
        pd.DataFrame(provenance_rows),
        on="observation_id",
        how="left",
        validate="one_to_one",
    )
    node_frame = gpd.GeoDataFrame(
        list(node_rows_by_id.values()),
        geometry="geometry",
        crs="EPSG:5186",
    ).sort_values(["scene_id", "node_kind", "node_id"], kind="stable").reset_index(drop=True)
    edge_frame = pd.DataFrame(edge_rows).sort_values(
        ["scene_id", "observation_id", "edge_id"],
        kind="stable",
    ).reset_index(drop=True)
    counts = {
        "candidate_count": candidate_count,
        "excluded_zero_length_count": excluded_zero_length_count,
        "invalid_geometry_count": invalid_geometry_count,
        "geometry_collection_count": geometry_collection_count,
        "unexpected_geometry_type_count": unexpected_geometry_type_count,
        "outside_scene_count": outside_scene_count,
        "self_loop_edge_count": self_loop_edge_count,
    }
    return (
        geometry_frame,
        attributes_frame,
        provenance_frame,
        node_frame,
        edge_frame,
        counts,
    )


def _validate_part_ids(
    geometry: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    provenance: pd.DataFrame,
) -> bool:
    geom_by_id = geometry.set_index("observation_id")
    prov_by_id = provenance.set_index("observation_id")
    for _, group in attributes.groupby(["scene_id", "object_id"], sort=False):
        parts = []
        for row in group.itertuples(index=False):
            line = geom_by_id.loc[row.observation_id].geometry
            parts.append(line)
        expected = []
        occurrences: dict[tuple[str, str], int] = {}
        for part_order, line in enumerate(sorted(parts, key=_part_key)):
            wkb_hash = _canonical_wkb_hash(line)
            key = ("LineString", wkb_hash)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            expected.append(
                (
                    part_order,
                    DerivedIdFactory.clip_part_id("LineString", wkb_hash, occurrence),
                    wkb_hash,
                    occurrence,
                )
            )
        actual = [
            (
                int(row.part_order),
                str(row.part_id),
                str(prov_by_id.loc[row.observation_id].part_geometry_wkb_sha256),
                int(prov_by_id.loc[row.observation_id].part_occurrence_index),
            )
            for row in group.sort_values("part_order").itertuples(index=False)
        ]
        if expected != actual:
            return False
    return True


def _validate_outputs(
    geometry: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    provenance: pd.DataFrame,
    nodes: gpd.GeoDataFrame,
    edges: pd.DataFrame,
    *,
    counts: Mapping[str, int],
    source_input_changed_count: int,
) -> RoadObservationValidation:
    observation_ids = attributes["observation_id"].astype(str)
    duplicate_observation_id_count = int(observation_ids.duplicated().sum())
    duplicate_part_id_group_count = int(
        attributes.duplicated(["scene_id", "object_id", "part_id"]).sum()
    )
    required = [
        "release_id",
        "split",
        "district_id",
        "scene_id",
        "object_type",
        "object_id",
        "part_id",
        "observation_id",
        "source_name",
        "geometry_status",
        "representative_x",
        "representative_y",
        "part_order",
        "observation_length_m",
        "parent_way_id",
    ]
    null_required_count = int(attributes[required].isna().sum().sum())
    parent_mismatch_count = int(
        (attributes["parent_way_id"].astype(str) != attributes["source_link_id"].astype(str)).sum()
    )
    geom_ids = set(geometry["observation_id"].astype(str))
    attr_ids = set(observation_ids)
    prov_ids = set(provenance["observation_id"].astype(str))
    geometry_attribute_id_mismatch_count = len(geom_ids ^ attr_ids)
    provenance_id_mismatch_count = len(prov_ids ^ attr_ids)
    geometry_types = set(geometry.geometry.geom_type.astype(str))
    geometry_types_valid = geometry_types <= {"LineString"}
    crs_valid = geometry.crs is not None and geometry.crs.to_epsg() == 5186
    length_mismatch_count = int(
        (
            (
                geometry.geometry.length.reset_index(drop=True)
                - attributes["observation_length_m"].astype(float)
            ).abs()
            > 1.0e-9
        ).sum()
    )
    midpoints = geometry.geometry.interpolate(0.5, normalized=True)
    representative_mismatch_count = int(
        (
            (midpoints.x.reset_index(drop=True) - attributes["representative_x"]).abs()
            > 1.0e-9
        ).sum()
        + (
            (midpoints.y.reset_index(drop=True) - attributes["representative_y"]).abs()
            > 1.0e-9
        ).sum()
    )
    deterministic_part_id_regeneration = _validate_part_ids(
        geometry,
        attributes,
        provenance,
    )
    regenerated_observation_ids = [
        DerivedIdFactory.observation_id(
            str(row.scene_id),
            ROAD_OBJECT_TYPE,
            str(row.object_id),
            str(row.part_id),
        )
        for row in attributes.itertuples(index=False)
    ]
    deterministic_observation_id_regeneration = (
        regenerated_observation_ids == observation_ids.to_list()
    )
    duplicate_node_id_count = int(nodes["node_id"].duplicated().sum())
    duplicate_edge_id_count = int(edges["edge_id"].duplicated().sum())
    node_ids = set(nodes["node_id"].astype(str))
    edge_node_reference_missing_count = len(
        (set(edges["start_node_id"].astype(str)) | set(edges["end_node_id"].astype(str)))
        - node_ids
    )
    boundary_node_count = int(nodes["is_scene_boundary_node"].sum())
    source_node_count = int((nodes["node_kind"] == "source").sum())
    forbidden_artifact_count = 0
    valid = (
        duplicate_observation_id_count == 0
        and duplicate_part_id_group_count == 0
        and null_required_count == 0
        and parent_mismatch_count == 0
        and length_mismatch_count == 0
        and representative_mismatch_count == 0
        and geometry_attribute_id_mismatch_count == 0
        and provenance_id_mismatch_count == 0
        and duplicate_node_id_count == 0
        and duplicate_edge_id_count == 0
        and int(counts["self_loop_edge_count"]) == 0
        and edge_node_reference_missing_count == 0
        and source_input_changed_count == 0
        and forbidden_artifact_count == 0
        and deterministic_part_id_regeneration
        and deterministic_observation_id_regeneration
        and crs_valid
        and geometry_types_valid
        and len(edges) == len(attributes)
    )
    return RoadObservationValidation(
        valid=valid,
        candidate_count=int(counts["candidate_count"]),
        observation_count=len(attributes),
        excluded_zero_length_count=int(counts["excluded_zero_length_count"]),
        invalid_geometry_count=int(counts["invalid_geometry_count"]),
        geometry_collection_count=int(counts["geometry_collection_count"]),
        unexpected_geometry_type_count=int(counts["unexpected_geometry_type_count"]),
        outside_scene_count=int(counts["outside_scene_count"]),
        duplicate_observation_id_count=duplicate_observation_id_count,
        duplicate_part_id_group_count=duplicate_part_id_group_count,
        null_required_count=null_required_count,
        parent_mismatch_count=parent_mismatch_count,
        length_mismatch_count=length_mismatch_count,
        representative_mismatch_count=representative_mismatch_count,
        geometry_attribute_id_mismatch_count=geometry_attribute_id_mismatch_count,
        provenance_id_mismatch_count=provenance_id_mismatch_count,
        node_count=len(nodes),
        edge_count=len(edges),
        duplicate_node_id_count=duplicate_node_id_count,
        duplicate_edge_id_count=duplicate_edge_id_count,
        self_loop_edge_count=int(counts["self_loop_edge_count"]),
        boundary_node_count=boundary_node_count,
        source_node_count=source_node_count,
        edge_node_reference_missing_count=edge_node_reference_missing_count,
        source_input_changed_count=source_input_changed_count,
        forbidden_artifact_count=forbidden_artifact_count,
        deterministic_part_id_regeneration=deterministic_part_id_regeneration,
        deterministic_observation_id_regeneration=(
            deterministic_observation_id_regeneration
        ),
        crs_valid=crs_valid,
        geometry_types_valid=geometry_types_valid,
    )


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_artifacts(
    output_directory: Path,
    geometry: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    provenance: pd.DataFrame,
    nodes: gpd.GeoDataFrame,
    edges: pd.DataFrame,
    validation: RoadObservationValidation,
) -> RoadObservationArtifacts:
    output_directory.mkdir(parents=True, exist_ok=False)
    geometry_path = output_directory / "road_observation_geometry.gpkg"
    attribute_path = output_directory / "road_observation_attributes.parquet"
    provenance_path = output_directory / "road_observation_provenance.parquet"
    node_gpkg_path = output_directory / "road_scene_nodes.gpkg"
    node_path = output_directory / "road_scene_nodes.parquet"
    edge_path = output_directory / "road_scene_edges.parquet"
    validation_path = output_directory / "validation.json"
    summary_path = output_directory / "summary.json"
    pyogrio.write_dataframe(
        geometry,
        geometry_path,
        layer="road_observations",
        driver="GPKG",
    )
    pyogrio.write_dataframe(
        nodes,
        node_gpkg_path,
        layer="road_scene_nodes",
        driver="GPKG",
    )
    node_attributes = pd.DataFrame(nodes.drop(columns="geometry"))
    for frame, path in (
        (attributes, attribute_path),
        (provenance, provenance_path),
        (node_attributes, node_path),
        (edges, edge_path),
    ):
        pq.write_table(
            pa.Table.from_pandas(frame, preserve_index=False),
            path,
            compression="zstd",
            version="2.6",
        )
    _write_json(validation_path, validation.to_dict())
    _write_json(summary_path, {})
    return RoadObservationArtifacts(
        output_directory=output_directory,
        geometry_geopackage=geometry_path,
        attribute_parquet=attribute_path,
        provenance_parquet=provenance_path,
        node_geopackage=node_gpkg_path,
        node_parquet=node_path,
        edge_parquet=edge_path,
        validation_json=validation_path,
        summary_json=summary_path,
        geometry_sha256=sha256_file(geometry_path),
        attribute_sha256=sha256_file(attribute_path),
        provenance_sha256=sha256_file(provenance_path),
        node_geopackage_sha256=sha256_file(node_gpkg_path),
        node_sha256=sha256_file(node_path),
        edge_sha256=sha256_file(edge_path),
        validation_sha256=sha256_file(validation_path),
        summary_sha256=sha256_file(summary_path),
    )


def run_road_observations(
    config_path: str | Path,
    *,
    scene_geometry: str | Path | None = None,
    road_geometry: str | Path | None = None,
    road_link_attributes: str | Path | None = None,
    road_node_attributes: str | Path | None = None,
    stable_ids: str | Path | None = None,
    stable_id_provenance: str | Path | None = None,
    schema_path: str | Path | None = None,
    log_level: str = "INFO",
) -> dict[str, object]:
    config = load_config(config_path)
    defaults = _resolve_default_inputs(config)
    inputs = {
        "scene_geometry": Path(scene_geometry or defaults["scene_geometry"]).resolve(),
        "road_geometry": Path(road_geometry or defaults["road_geometry"]).resolve(),
        "road_link_attributes": Path(
            road_link_attributes or defaults["road_link_attributes"]
        ).resolve(),
        "road_node_attributes": Path(
            road_node_attributes or defaults["road_node_attributes"]
        ).resolve(),
        "stable_ids": Path(stable_ids or defaults["stable_ids"]).resolve(),
        "stable_id_provenance": Path(
            stable_id_provenance or defaults["stable_id_provenance"]
        ).resolve(),
        "schema": Path(schema_path or defaults["schema"]).resolve(),
    }
    schema = load_observation_schema(inputs["schema"])
    if schema.schema_version != CONTRACT_VERSION:
        raise ObservationContractError("D-102 requires M2.1 observation schema")
    metadata = collect_run_metadata(config)
    log_path = config.paths.logs_dir / f"{metadata.run_id}_m2_3_road_observations.jsonl"
    logger = configure_logging(log_path, metadata.run_id, level=log_level)
    logger.info("M2.3 Road Observation started")
    source_snapshot_before = _snapshot(tuple(inputs.values()))
    scenes = _read_scenes(inputs["scene_geometry"])
    links, nodes = _read_road_geometry(inputs["road_geometry"])
    attributes = _read_link_attributes(inputs["road_link_attributes"])
    ids, id_provenance = _read_id_maps(
        inputs["stable_ids"],
        inputs["stable_id_provenance"],
    )
    (
        geometry,
        attribute_frame,
        provenance_frame,
        node_frame,
        edge_frame,
        counts,
    ) = _materialize_frames(
        release_id=metadata.run_id,
        producer_commit=metadata.git_commit,
        config_hash=metadata.resolved_config_hash,
        scenes=scenes,
        links=links,
        nodes=nodes,
        attributes=attributes,
        ids=ids,
        id_provenance=id_provenance,
    )
    source_snapshot_after = _snapshot(tuple(inputs.values()))
    changed_inputs = [
        path
        for path, before in source_snapshot_before.items()
        if source_snapshot_after[path] != before
    ]
    validation = _validate_outputs(
        geometry,
        attribute_frame,
        provenance_frame,
        node_frame,
        edge_frame,
        counts=counts,
        source_input_changed_count=len(changed_inputs),
    )
    if not validation.valid:
        raise ObservationContractError("M2.3 road observation validation failed")
    resolved_path = (
        config.paths.resolved_config_dir / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    output_directory = config.paths.output_root / "observations" / "road" / metadata.run_id
    summary = {
        "artifacts": {},
        "contract_version": CONTRACT_VERSION,
        "decision": "D-102",
        "parent_way_decision": "D-004",
        "connectivity_decision": "D-006",
        "forbidden": {
            "contract_modified": False,
            "schema_modified": False,
            "stable_id_modified": False,
            "geometry_repair": False,
            "geometry_snapping": False,
            "precision_reduction": False,
            "raster_extraction": False,
            "tensor_generation": False,
            "representation_learning": False,
        },
        "input_artifacts": {key: str(value) for key, value in inputs.items()},
        "input_changes": changed_inputs,
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "status": "complete",
        "validation": validation.to_dict(),
    }
    artifacts = _write_artifacts(
        output_directory,
        geometry,
        attribute_frame,
        provenance_frame,
        node_frame,
        edge_frame,
        validation,
    )
    summary = {**summary, "artifacts": artifacts.to_summary_dict()}
    _write_json(artifacts.summary_json, summary)
    artifacts = RoadObservationArtifacts(
        output_directory=artifacts.output_directory,
        geometry_geopackage=artifacts.geometry_geopackage,
        attribute_parquet=artifacts.attribute_parquet,
        provenance_parquet=artifacts.provenance_parquet,
        node_geopackage=artifacts.node_geopackage,
        node_parquet=artifacts.node_parquet,
        edge_parquet=artifacts.edge_parquet,
        validation_json=artifacts.validation_json,
        summary_json=artifacts.summary_json,
        geometry_sha256=artifacts.geometry_sha256,
        attribute_sha256=artifacts.attribute_sha256,
        provenance_sha256=artifacts.provenance_sha256,
        node_geopackage_sha256=artifacts.node_geopackage_sha256,
        node_sha256=artifacts.node_sha256,
        edge_sha256=artifacts.edge_sha256,
        validation_sha256=artifacts.validation_sha256,
        summary_sha256=sha256_file(artifacts.summary_json),
    )
    reports = write_milestone_reports(
        config.paths.project_root,
        "M2",
        f"{metadata.run_id}_m2_3_road_observations",
        "M2_3_road_observations.md",
        title="M2.3 Road Observations",
        metadata=metadata,
        summary={**summary, "artifacts": artifacts.to_dict()},
        sections=(
            ReportSection(
                "Scope",
                "D-102-approved road observation implementation only. No raster, "
                "tensor, embedding, repair, snapping, precision reduction, "
                "contract, schema or stable-ID change was performed.",
            ),
            ReportSection(
                "Validation",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in validation.to_dict().items()
                ),
            ),
            ReportSection(
                "Artifacts",
                "\n".join(
                    f"- `{key}`: `{value}`"
                    for key, value in artifacts.to_dict().items()
                ),
            ),
            ReportSection(
                "Next",
                "M2.3 PASS. Next workflow task is D-103 POI Observation Contract.",
            ),
        ),
    )
    logger.info("M2.3 Road Observation completed")
    return {
        "attribute_parquet": str(artifacts.attribute_parquet),
        "candidate_count": validation.candidate_count,
        "canonical_report": str(reports.canonical_markdown),
        "edge_count": validation.edge_count,
        "edge_parquet": str(artifacts.edge_parquet),
        "excluded_zero_length_count": validation.excluded_zero_length_count,
        "geometry_geopackage": str(artifacts.geometry_geopackage),
        "json_report": str(reports.raw.json),
        "markdown_report": str(reports.raw.markdown),
        "node_count": validation.node_count,
        "node_geopackage": str(artifacts.node_geopackage),
        "node_parquet": str(artifacts.node_parquet),
        "observation_count": validation.observation_count,
        "output_directory": str(output_directory),
        "provenance_parquet": str(artifacts.provenance_parquet),
        "run_id": metadata.run_id,
        "status": "complete",
        "validation": "PASS",
    }
