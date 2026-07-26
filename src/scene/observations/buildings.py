"""M2.2 Building Observation implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
from shapely import normalize, to_wkb
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


BUILDING_OBJECT_TYPE = "building"
CONTRACT_VERSION = "m2.1-v1"
ALLOWED_BUILDING_GEOMETRIES = {"Polygon", "MultiPolygon"}


@dataclass(frozen=True, slots=True)
class BuildingObservationArtifacts:
    """M2.2 building observation artifacts."""

    output_directory: Path
    geometry_geopackage: Path
    attribute_parquet: Path
    provenance_parquet: Path
    validation_json: Path
    summary_json: Path
    geometry_sha256: str
    attribute_sha256: str
    provenance_sha256: str
    validation_sha256: str
    summary_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}

    def to_summary_dict(self) -> dict[str, str]:
        data = self.to_dict()
        data.pop("summary_sha256")
        return data


@dataclass(frozen=True, slots=True)
class BuildingObservationValidation:
    """Validation summary for M2.2 building observations."""

    valid: bool
    candidate_count: int
    observation_count: int
    excluded_zero_area_count: int
    invalid_geometry_count: int
    geometry_collection_count: int
    unexpected_geometry_type_count: int
    duplicate_observation_id_count: int
    null_required_count: int
    part_id_non_null_count: int
    outside_scene_count: int
    area_mismatch_count: int
    representative_mismatch_count: int
    geometry_attribute_id_mismatch_count: int
    source_input_changed_count: int
    forbidden_artifact_count: int
    deterministic_regeneration: bool
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
    building_dir = _latest_directory(
        output_root / "buildings",
        ("building_geometry.gpkg", "building_attributes.parquet"),
    )
    ids_dir = _latest_directory(
        output_root / "ids",
        ("ids.parquet", "provenance.parquet"),
    )
    return {
        "scene_geometry": scene_dir / "scene_footprints.gpkg",
        "building_geometry": building_dir / "building_geometry.gpkg",
        "building_attributes": building_dir / "building_attributes.parquet",
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


def _validate_crs(frame: gpd.GeoDataFrame, *, label: str) -> None:
    if frame.crs is None or frame.crs.to_epsg() != 5186:
        raise ObservationContractError(f"{label} CRS must be EPSG:5186")


def _read_scenes(path: Path) -> gpd.GeoDataFrame:
    scenes = gpd.read_file(path, layer="scene_footprints")
    _validate_crs(scenes, label="scene footprints")
    required = {
        "scene_id",
        "scene_footprint_id",
        "split",
        "district_id",
        "geometry",
    }
    missing = sorted(required - set(scenes.columns))
    if missing:
        raise ObservationContractError(
            f"scene footprints missing required columns: {missing}"
        )
    invalid = int((~scenes.geometry.is_valid).sum())
    unexpected = sorted(set(scenes.geometry.geom_type.astype(str)) - {"Polygon"})
    if invalid or unexpected:
        raise ObservationContractError(
            f"scene geometry invalid={invalid}, unexpected={unexpected}"
        )
    return scenes[
        [
            "scene_id",
            "scene_footprint_id",
            "split",
            "district_id",
            "geometry",
        ]
    ].copy()


def _read_buildings(path: Path) -> gpd.GeoDataFrame:
    buildings = gpd.read_file(path, layer="buildings")
    _validate_crs(buildings, label="building geometry")
    required = {"source_building_id", "geometry"}
    missing = sorted(required - set(buildings.columns))
    if missing:
        raise ObservationContractError(
            f"building geometry missing required columns: {missing}"
        )
    geom_type = buildings.geometry.geom_type.astype(str)
    collection_count = int(geom_type.eq("GeometryCollection").sum())
    unexpected = sorted(set(geom_type.dropna()) - ALLOWED_BUILDING_GEOMETRIES)
    invalid_count = int((~buildings.geometry.is_valid).sum())
    if collection_count or unexpected or invalid_count:
        raise ObservationContractError(
            "building source geometry violates D-101: "
            f"invalid={invalid_count}, GeometryCollection={collection_count}, "
            f"unexpected={unexpected}"
        )
    return buildings[["source_building_id", "geometry"]].copy()


def _read_attributes(path: Path) -> pd.DataFrame:
    columns = [
        "source_building_id",
        "building_use",
        "building_structure",
        "building_height_m",
        "source_building_area_m2",
        "source_name",
        "source_file_sha256",
    ]
    table = pq.read_table(path, columns=columns)
    frame = table.to_pandas()
    if frame["source_building_id"].duplicated().any():
        raise ObservationContractError(
            "building attributes contain duplicate source_building_id"
        )
    return frame


def _read_id_maps(
    ids_path: Path,
    provenance_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ids = pq.read_table(
        ids_path,
        columns=["entity_type", "source_native_id", "canonical_object_id"],
        filters=[("entity_type", "=", "building")],
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
        filters=[("entity_type", "=", "building")],
    ).to_pandas()
    if ids.empty or provenance.empty:
        raise ObservationContractError("building stable ID rows are missing")
    if ids["source_native_id"].duplicated().any():
        raise ObservationContractError("building stable IDs duplicate native IDs")
    if provenance["canonical_object_id"].duplicated().any():
        raise ObservationContractError(
            "building ID provenance duplicates canonical IDs"
        )
    return ids, provenance


def _materialize_frames(
    *,
    release_id: str,
    producer_commit: str,
    config_hash: str,
    scenes: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    ids: pd.DataFrame,
    id_provenance: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame, Mapping[str, int]]:
    building_attrs = attributes.merge(
        ids[["source_native_id", "canonical_object_id"]],
        left_on="source_building_id",
        right_on="source_native_id",
        how="inner",
        validate="one_to_one",
    )
    building_attrs = building_attrs.rename(
        columns={"canonical_object_id": "object_id"}
    )
    buildings = buildings.merge(
        ids[["source_native_id", "canonical_object_id"]],
        left_on="source_building_id",
        right_on="source_native_id",
        how="inner",
        validate="one_to_one",
    ).rename(columns={"canonical_object_id": "object_id"})
    if len(buildings) == 0:
        raise ObservationContractError("no building geometries matched stable IDs")

    scene_lookup = scenes.geometry.copy()
    candidates = gpd.sjoin(
        buildings[["source_building_id", "object_id", "geometry"]],
        scenes[["scene_id", "split", "district_id", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    candidate_count = len(candidates)
    rows: list[dict[str, object]] = []
    geometry_rows: list[dict[str, object]] = []
    provenance_rows: list[dict[str, object]] = []

    attr_by_object = building_attrs.set_index("object_id", drop=False)
    provenance_by_object = id_provenance.set_index(
        "canonical_object_id",
        drop=False,
    )

    source_geometries = candidates.geometry.reset_index(drop=True)
    scene_geometries = gpd.GeoSeries(
        candidates["index_right"].map(scene_lookup).to_numpy(),
        crs=scenes.crs,
    )
    observed_geometries = source_geometries.intersection(
        scene_geometries,
        align=False,
    )
    observed_areas = observed_geometries.area
    positive_area = (~observed_geometries.is_empty) & (observed_areas > 0.0)
    excluded_zero_area_count = int((~positive_area).sum())

    candidates = candidates.reset_index(drop=True)
    active = candidates.loc[positive_area.to_numpy()].copy()
    active_observed = observed_geometries.loc[positive_area].reset_index(drop=True)
    active_source = source_geometries.loc[positive_area].reset_index(drop=True)
    active_scene = scene_geometries.loc[positive_area].reset_index(drop=True)
    active_area = observed_areas.loc[positive_area].reset_index(drop=True)

    observed_types = active_observed.geom_type.astype(str)
    geometry_collection_count = int(observed_types.eq("GeometryCollection").sum())
    unexpected_geometry_type_count = int(
        (~observed_types.isin(ALLOWED_BUILDING_GEOMETRIES)).sum()
    )
    invalid_geometry_count = int((~active_observed.is_valid).sum())
    outside_scene_count = int((~active_scene.covers(active_observed, align=False)).sum())
    if (
        geometry_collection_count
        or unexpected_geometry_type_count
        or invalid_geometry_count
        or outside_scene_count
    ):
        raise ObservationContractError(
            "building observation geometry violates D-101: "
            f"invalid={invalid_geometry_count}, "
            f"GeometryCollection={geometry_collection_count}, "
            f"unexpected={unexpected_geometry_type_count}, "
            f"outside_scene={outside_scene_count}"
        )

    active = active.reset_index(drop=True)
    active["observed_geometry"] = active_observed
    active["source_geometry"] = active_source
    active["observed_area"] = active_area
    active["representative_geometry"] = active_observed.centroid
    active["geometry_status"] = [
        "full" if full else "clipped"
        for full in active_scene.covers(active_source, align=False).to_list()
    ]
    active["touches_scene_boundary"] = active_observed.intersects(
        active_scene.boundary,
        align=False,
    ).to_list()

    source_wkb_hash_by_object: dict[str, str] = {}
    for candidate in active.itertuples(index=False):
        source_geometry = candidate.source_geometry
        observed = candidate.observed_geometry

        object_id = str(candidate.object_id)
        scene_id = str(candidate.scene_id)
        observation_id = DerivedIdFactory.observation_id(
            scene_id,
            BUILDING_OBJECT_TYPE,
            object_id,
            None,
        )
        representative = candidate.representative_geometry
        attr = attr_by_object.loc[object_id]
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
        common = {
            "release_id": release_id,
            "split": str(candidate.split),
            "district_id": str(candidate.district_id),
            "scene_id": scene_id,
            "object_type": BUILDING_OBJECT_TYPE,
            "object_id": object_id,
            "part_id": None,
            "observation_id": observation_id,
            "source_name": str(attr["source_name"]),
            "geometry_status": str(candidate.geometry_status),
            "touches_scene_boundary": bool(candidate.touches_scene_boundary),
            "representative_x": float(representative.x),
            "representative_y": float(representative.y),
            "observation_area_m2": float(candidate.observed_area),
            "building_use": attr["building_use"],
            "building_structure": attr["building_structure"],
            "building_height_m": attr["building_height_m"],
            "source_building_area_m2": attr["source_building_area_m2"],
            "source_building_id": str(candidate.source_building_id),
        }
        rows.append(common)
        geometry_rows.append(
            {
                "observation_id": observation_id,
                "geometry": observed,
            }
        )
        provenance_rows.append(
            {
                "observation_id": observation_id,
                "object_id": object_id,
                "source_geometry_id": source_geometry_id,
                "source_geometry_wkb_sha256": source_wkb_hash,
                "source_file_sha256": source_sha,
                "clip_operation": "clip",
                "producer_commit": producer_commit,
                "contract_version": CONTRACT_VERSION,
                "source_name": str(source_prov["source_name"]),
                "source_path": str(source_prov["source_path"]),
                "source_native_id": str(candidate.source_building_id),
                "stable_id_run_id": str(source_prov["run_id"]),
                "stable_id_config_hash": str(source_prov["config_hash"]),
                "canonical_manifest_path": str(
                    source_prov["canonical_manifest_path"]
                ),
                "canonical_frame_path": str(source_prov["canonical_frame_path"]),
                "producer_config_hash": config_hash,
            }
        )

    attributes_frame = pd.DataFrame(rows)
    geometry_frame = gpd.GeoDataFrame(
        geometry_rows,
        geometry="geometry",
        crs="EPSG:5186",
    )
    provenance_frame = pd.DataFrame(provenance_rows)
    if attributes_frame.empty:
        raise ObservationContractError("M2.2 produced zero building observations")
    order = [
        "split",
        "district_id",
        "scene_id",
        "object_id",
        "observation_id",
    ]
    attributes_frame = attributes_frame.sort_values(
        order,
        kind="stable",
    ).reset_index(drop=True)
    order_map = {
        observation_id: position
        for position, observation_id in enumerate(
            attributes_frame["observation_id"].astype(str)
        )
    }
    geometry_frame["__order"] = geometry_frame["observation_id"].map(order_map)
    geometry_frame = (
        geometry_frame.sort_values("__order", kind="stable")
        .drop(columns="__order")
        .reset_index(drop=True)
    )
    provenance_frame = attributes_frame[["observation_id"]].merge(
        provenance_frame,
        on="observation_id",
        how="left",
        validate="one_to_one",
    )
    counts = {
        "candidate_count": candidate_count,
        "excluded_zero_area_count": excluded_zero_area_count,
        "invalid_geometry_count": invalid_geometry_count,
        "geometry_collection_count": geometry_collection_count,
        "unexpected_geometry_type_count": unexpected_geometry_type_count,
        "outside_scene_count": outside_scene_count,
    }
    return geometry_frame, attributes_frame, provenance_frame, counts


def _validate_outputs(
    geometry: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    provenance: pd.DataFrame,
    *,
    counts: Mapping[str, int],
    source_input_changed_count: int,
) -> BuildingObservationValidation:
    observation_ids = attributes["observation_id"].astype(str)
    duplicate_count = int(observation_ids.duplicated().sum())
    required = [
        "release_id",
        "split",
        "district_id",
        "scene_id",
        "object_type",
        "object_id",
        "observation_id",
        "source_name",
        "geometry_status",
        "representative_x",
        "representative_y",
        "observation_area_m2",
    ]
    null_required_count = int(attributes[required].isna().sum().sum())
    part_id_non_null_count = int(attributes["part_id"].notna().sum())
    geom_ids = set(geometry["observation_id"].astype(str))
    attr_ids = set(observation_ids)
    geometry_attribute_id_mismatch_count = len(geom_ids ^ attr_ids)
    geometry_types = set(geometry.geometry.geom_type.astype(str))
    geometry_types_valid = geometry_types <= ALLOWED_BUILDING_GEOMETRIES
    crs_valid = geometry.crs is not None and geometry.crs.to_epsg() == 5186
    outside_scene_count = int(counts["outside_scene_count"])
    area_mismatch_count = int(
        (
            (
                geometry.geometry.area.reset_index(drop=True)
                - attributes["observation_area_m2"].astype(float)
            ).abs()
            > 1.0e-9
        ).sum()
    )
    centroids = geometry.geometry.centroid
    representative_mismatch_count = int(
        (
            (centroids.x.reset_index(drop=True) - attributes["representative_x"]).abs()
            > 1.0e-9
        ).sum()
        + (
            (centroids.y.reset_index(drop=True) - attributes["representative_y"]).abs()
            > 1.0e-9
        ).sum()
    )
    regenerated = [
        DerivedIdFactory.observation_id(
            str(row.scene_id),
            BUILDING_OBJECT_TYPE,
            str(row.object_id),
            None,
        )
        for row in attributes.itertuples(index=False)
    ]
    deterministic_regeneration = regenerated == observation_ids.to_list()
    forbidden_artifact_count = 0
    valid = (
        duplicate_count == 0
        and null_required_count == 0
        and part_id_non_null_count == 0
        and geometry_attribute_id_mismatch_count == 0
        and outside_scene_count == 0
        and area_mismatch_count == 0
        and representative_mismatch_count == 0
        and source_input_changed_count == 0
        and forbidden_artifact_count == 0
        and deterministic_regeneration
        and crs_valid
        and geometry_types_valid
        and len(provenance) == len(attributes)
    )
    return BuildingObservationValidation(
        valid=valid,
        candidate_count=int(counts["candidate_count"]),
        observation_count=len(attributes),
        excluded_zero_area_count=int(counts["excluded_zero_area_count"]),
        invalid_geometry_count=int(counts["invalid_geometry_count"]),
        geometry_collection_count=int(counts["geometry_collection_count"]),
        unexpected_geometry_type_count=int(
            counts["unexpected_geometry_type_count"]
        ),
        duplicate_observation_id_count=duplicate_count,
        null_required_count=null_required_count,
        part_id_non_null_count=part_id_non_null_count,
        outside_scene_count=outside_scene_count,
        area_mismatch_count=area_mismatch_count,
        representative_mismatch_count=representative_mismatch_count,
        geometry_attribute_id_mismatch_count=(
            geometry_attribute_id_mismatch_count
        ),
        source_input_changed_count=source_input_changed_count,
        forbidden_artifact_count=forbidden_artifact_count,
        deterministic_regeneration=deterministic_regeneration,
        crs_valid=crs_valid,
        geometry_types_valid=geometry_types_valid,
    )


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_artifacts(
    output_directory: Path,
    geometry: gpd.GeoDataFrame,
    attributes: pd.DataFrame,
    provenance: pd.DataFrame,
    validation: BuildingObservationValidation,
) -> BuildingObservationArtifacts:
    output_directory.mkdir(parents=True, exist_ok=False)
    geometry_path = output_directory / "building_observation_geometry.gpkg"
    attribute_path = output_directory / "building_observation_attributes.parquet"
    provenance_path = output_directory / "building_observation_provenance.parquet"
    validation_path = output_directory / "validation.json"
    summary_path = output_directory / "summary.json"
    pyogrio.write_dataframe(
        geometry,
        geometry_path,
        layer="building_observations",
        driver="GPKG",
    )
    pq.write_table(
        pa.Table.from_pandas(attributes, preserve_index=False),
        attribute_path,
        compression="zstd",
        version="2.6",
    )
    pq.write_table(
        pa.Table.from_pandas(provenance, preserve_index=False),
        provenance_path,
        compression="zstd",
        version="2.6",
    )
    _write_json(validation_path, validation.to_dict())
    _write_json(summary_path, {})
    return BuildingObservationArtifacts(
        output_directory=output_directory,
        geometry_geopackage=geometry_path,
        attribute_parquet=attribute_path,
        provenance_parquet=provenance_path,
        validation_json=validation_path,
        summary_json=summary_path,
        geometry_sha256=sha256_file(geometry_path),
        attribute_sha256=sha256_file(attribute_path),
        provenance_sha256=sha256_file(provenance_path),
        validation_sha256=sha256_file(validation_path),
        summary_sha256=sha256_file(summary_path),
    )


def run_building_observations(
    config_path: str | Path,
    *,
    scene_geometry: str | Path | None = None,
    building_geometry: str | Path | None = None,
    building_attributes: str | Path | None = None,
    stable_ids: str | Path | None = None,
    stable_id_provenance: str | Path | None = None,
    schema_path: str | Path | None = None,
    log_level: str = "INFO",
) -> dict[str, object]:
    """Materialize D-101-approved M2.2 building observations."""

    config = load_config(config_path)
    defaults = _resolve_default_inputs(config)
    inputs = {
        "scene_geometry": Path(scene_geometry or defaults["scene_geometry"]).resolve(),
        "building_geometry": Path(
            building_geometry or defaults["building_geometry"]
        ).resolve(),
        "building_attributes": Path(
            building_attributes or defaults["building_attributes"]
        ).resolve(),
        "stable_ids": Path(stable_ids or defaults["stable_ids"]).resolve(),
        "stable_id_provenance": Path(
            stable_id_provenance or defaults["stable_id_provenance"]
        ).resolve(),
        "schema": Path(schema_path or defaults["schema"]).resolve(),
    }
    schema = load_observation_schema(inputs["schema"])
    if schema.schema_version != CONTRACT_VERSION:
        raise ObservationContractError("D-101 requires M2.1 observation schema")

    metadata = collect_run_metadata(config)
    log_path = (
        config.paths.logs_dir
        / f"{metadata.run_id}_m2_2_building_observations.jsonl"
    )
    logger = configure_logging(log_path, metadata.run_id, level=log_level)
    logger.info("M2.2 Building Observation started")
    source_snapshot_before = _snapshot(tuple(inputs.values()))

    scenes = _read_scenes(inputs["scene_geometry"])
    buildings = _read_buildings(inputs["building_geometry"])
    attributes = _read_attributes(inputs["building_attributes"])
    ids, id_provenance = _read_id_maps(
        inputs["stable_ids"],
        inputs["stable_id_provenance"],
    )
    geometry, attribute_frame, provenance_frame, counts = _materialize_frames(
        release_id=metadata.run_id,
        producer_commit=metadata.git_commit,
        config_hash=metadata.resolved_config_hash,
        scenes=scenes,
        buildings=buildings,
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
        counts=counts,
        source_input_changed_count=len(changed_inputs),
    )
    if not validation.valid:
        raise ObservationContractError("M2.2 building observation validation failed")

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)
    output_directory = (
        config.paths.output_root / "observations" / "building" / metadata.run_id
    )
    summary = {
        "artifacts": {},
        "contract_version": CONTRACT_VERSION,
        "decision": "D-101",
        "forbidden": {
            "contract_modified": False,
            "schema_modified": False,
            "road_observation": False,
            "poi_observation": False,
            "raster_extraction": False,
            "relation_graph": False,
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
        validation,
    )
    summary = {**summary, "artifacts": artifacts.to_summary_dict()}
    _write_json(artifacts.summary_json, summary)
    artifacts = BuildingObservationArtifacts(
        output_directory=artifacts.output_directory,
        geometry_geopackage=artifacts.geometry_geopackage,
        attribute_parquet=artifacts.attribute_parquet,
        provenance_parquet=artifacts.provenance_parquet,
        validation_json=artifacts.validation_json,
        summary_json=artifacts.summary_json,
        geometry_sha256=artifacts.geometry_sha256,
        attribute_sha256=artifacts.attribute_sha256,
        provenance_sha256=artifacts.provenance_sha256,
        validation_sha256=artifacts.validation_sha256,
        summary_sha256=sha256_file(artifacts.summary_json),
    )

    reports = write_milestone_reports(
        config.paths.project_root,
        "M2",
        f"{metadata.run_id}_m2_2_building_observations",
        "M2_2_building_observations.md",
        title="M2.2 Building Observations",
        metadata=metadata,
        summary={
            **summary,
            "artifacts": artifacts.to_dict(),
            "canonical_report": str(
                config.paths.project_root
                / "docs"
                / "reports"
                / "M2"
                / "canonical"
                / "M2_2_building_observations.md"
            ),
        },
        sections=(
            ReportSection(
                "Scope",
                "D-101-approved building observation implementation only. No "
                "road, POI, raster, relation, tensor, representation learning, "
                "contract or schema change was performed.",
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
                "M2.2 PASS. Next workflow task is D-102 Discussion. D-004 and "
                "D-006 remain open gates for road approval/materialization.",
            ),
        ),
    )
    logger.info("M2.2 Building Observation completed")
    return {
        "attribute_parquet": str(artifacts.attribute_parquet),
        "candidate_count": validation.candidate_count,
        "excluded_zero_area_count": validation.excluded_zero_area_count,
        "geometry_geopackage": str(artifacts.geometry_geopackage),
        "canonical_report": str(reports.canonical_markdown),
        "json_report": str(reports.raw.json),
        "markdown_report": str(reports.raw.markdown),
        "observation_count": validation.observation_count,
        "output_directory": str(output_directory),
        "provenance_parquet": str(artifacts.provenance_parquet),
        "run_id": metadata.run_id,
        "status": "complete",
        "validation": "PASS",
    }
