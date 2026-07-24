"""Synthetic-only reference evaluator for the M2.1 observation contract."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from shapely import from_wkt, normalize, to_wkb
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
import yaml

from scene.id.generator import DerivedIdFactory
from scene.observations.exceptions import (
    ObservationContractError,
    ObservationGeometryError,
)
from scene.observations.schema import ObservationSchema


OBJECT_ORDER = {"building": 0, "road": 1, "poi": 2}


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """One logical synthetic vector observation."""

    release_id: str
    split: str
    district_id: str
    scene_id: str
    object_type: str
    object_id: str
    part_id: str | None
    observation_id: str
    source_name: str
    geometry_status: str
    touches_scene_boundary: bool
    representative_x: float
    representative_y: float
    observation_area_m2: float | None
    observation_length_m: float | None
    part_order: int | None
    parent_way_id: str | None
    is_scene_boundary_endpoint: bool | None
    geometry_wkb_sha256: str


@dataclass(frozen=True, slots=True)
class FixtureValidationResult:
    """Machine-readable result of synthetic contract validation."""

    valid: bool
    fixture_name: str
    observation_count: int
    count_by_scene_and_type: Mapping[str, Mapping[str, int]]
    deterministic_regeneration: bool
    expected_output_match: bool
    invalid_geometry_hard_failures: int
    geometry_collection_hard_failures: int
    overlapping_object_distinct_observation_ids: bool
    raster_nodata_distinct: bool
    content_hash: str
    source_access: bool
    records: tuple[ObservationRecord, ...]

    def to_dict(self, *, include_records: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "content_hash": self.content_hash,
            "count_by_scene_and_type": {
                scene_id: dict(counts)
                for scene_id, counts in self.count_by_scene_and_type.items()
            },
            "deterministic_regeneration": self.deterministic_regeneration,
            "expected_output_match": self.expected_output_match,
            "fixture_name": self.fixture_name,
            "geometry_collection_hard_failures": (
                self.geometry_collection_hard_failures
            ),
            "invalid_geometry_hard_failures": (
                self.invalid_geometry_hard_failures
            ),
            "observation_count": self.observation_count,
            "overlapping_object_distinct_observation_ids": (
                self.overlapping_object_distinct_observation_ids
            ),
            "raster_nodata_distinct": self.raster_nodata_distinct,
            "source_access": self.source_access,
            "valid": self.valid,
        }
        if include_records:
            result["records"] = [asdict(record) for record in self.records]
        return result


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObservationContractError(f"{label} must be a mapping")
    return value


def _canonical_wkb_hash(geometry: BaseGeometry) -> str:
    canonical = normalize(geometry)
    payload = to_wkb(
        canonical,
        byte_order=1,
        output_dimension=2,
        include_srid=False,
    )
    return hashlib.sha256(payload).hexdigest()


def _validate_geometry(
    geometry: BaseGeometry,
    allowed: set[str],
    *,
    label: str,
) -> None:
    if geometry.geom_type == "GeometryCollection":
        if geometry.is_empty:
            raise ObservationGeometryError(
                f"{label} GeometryCollection EMPTY is forbidden"
            )
        raise ObservationGeometryError(
            f"{label} GeometryCollection is forbidden"
        )
    if geometry.is_empty:
        raise ObservationGeometryError(f"{label} geometry is empty")
    if not geometry.is_valid:
        raise ObservationGeometryError(f"{label} geometry is invalid")
    if geometry.geom_type not in allowed:
        raise ObservationGeometryError(
            f"{label} geometry type {geometry.geom_type} is not allowed"
        )


def _road_part_key(line: LineString) -> tuple[float, float, float, str]:
    min_x, min_y, _, _ = line.bounds
    return (-float(line.length), float(min_x), float(min_y), _canonical_wkb_hash(line))


def _ordered_road_parts(geometry: BaseGeometry) -> tuple[tuple[LineString, str], ...]:
    if geometry.geom_type == "LineString":
        parts = [geometry]
    elif geometry.geom_type == "MultiLineString":
        parts = list(geometry.geoms)
    else:
        raise ObservationGeometryError(
            f"road clip geometry type {geometry.geom_type} is not allowed"
        )
    ordered = sorted(parts, key=_road_part_key)
    occurrences: defaultdict[str, int] = defaultdict(int)
    result: list[tuple[LineString, str]] = []
    for part in ordered:
        wkb_hash = _canonical_wkb_hash(part)
        occurrence = occurrences[wkb_hash]
        occurrences[wkb_hash] += 1
        part_id = DerivedIdFactory.clip_part_id(
            "LineString",
            wkb_hash,
            occurrence,
        )
        result.append((part, part_id))
    return tuple(result)


def _source_endpoints(geometry: BaseGeometry) -> set[tuple[float, float]]:
    lines: Iterable[LineString]
    if geometry.geom_type == "LineString":
        lines = (geometry,)
    else:
        lines = geometry.geoms
    return {
        (float(coordinate[0]), float(coordinate[1]))
        for line in lines
        for coordinate in (line.coords[0], line.coords[-1])
    }


def _record(
    *,
    scene: Mapping[str, Any],
    source: Mapping[str, Any],
    object_type: str,
    geometry: BaseGeometry,
    geometry_status: str,
    part_id: str | None,
    area: float | None,
    length: float | None,
    part_order: int | None = None,
    parent_way_id: str | None = None,
    is_scene_boundary_endpoint: bool | None = None,
) -> ObservationRecord:
    if not scene["geometry"].covers(geometry):
        raise ObservationGeometryError(
            "observed geometry extends outside the closed scene"
        )
    if object_type == "road":
        representative = geometry.interpolate(0.5, normalized=True)
    else:
        representative = geometry.centroid
    observation_id = DerivedIdFactory.observation_id(
        str(scene["scene_id"]),
        object_type,
        str(source["object_id"]),
        part_id,
    )
    return ObservationRecord(
        release_id=str(scene["release_id"]),
        split=str(scene["split"]),
        district_id=str(scene["district_id"]),
        scene_id=str(scene["scene_id"]),
        object_type=object_type,
        object_id=str(source["object_id"]),
        part_id=part_id,
        observation_id=observation_id,
        source_name=str(source["source_name"]),
        geometry_status=geometry_status,
        touches_scene_boundary=geometry.intersects(scene["geometry"].boundary),
        representative_x=float(representative.x),
        representative_y=float(representative.y),
        observation_area_m2=area,
        observation_length_m=length,
        part_order=part_order,
        parent_way_id=parent_way_id,
        is_scene_boundary_endpoint=is_scene_boundary_endpoint,
        geometry_wkb_sha256=_canonical_wkb_hash(geometry),
    )


def _observe_building(
    scene: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[ObservationRecord, ...]:
    geometry = source["geometry"]
    _validate_geometry(geometry, {"Polygon", "MultiPolygon"}, label="building source")
    observed = geometry.intersection(scene["geometry"])
    if observed.is_empty or observed.area <= 0.0:
        return ()
    _validate_geometry(
        observed,
        {"Polygon", "MultiPolygon"},
        label="building observation",
    )
    status = "full" if scene["geometry"].covers(geometry) else "clipped"
    return (
        _record(
            scene=scene,
            source=source,
            object_type="building",
            geometry=observed,
            geometry_status=status,
            part_id=None,
            area=float(observed.area),
            length=None,
        ),
    )


def _observe_road(
    scene: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[ObservationRecord, ...]:
    geometry = source["geometry"]
    _validate_geometry(
        geometry,
        {"LineString", "MultiLineString"},
        label="road source",
    )
    observed = geometry.intersection(scene["geometry"])
    if observed.is_empty or observed.length <= 0.0:
        return ()
    _validate_geometry(
        observed,
        {"LineString", "MultiLineString"},
        label="road observation",
    )
    parts = _ordered_road_parts(observed)
    if len(parts) > 1:
        status = "split_by_clip"
    else:
        status = "full" if scene["geometry"].covers(geometry) else "clipped"
    source_endpoints = _source_endpoints(geometry)
    records: list[ObservationRecord] = []
    for part_order, (part, part_id) in enumerate(parts):
        endpoints = {
            (float(part.coords[0][0]), float(part.coords[0][1])),
            (float(part.coords[-1][0]), float(part.coords[-1][1])),
        }
        created_boundary_endpoint = any(
            point not in source_endpoints
            and scene["geometry"].boundary.covers(Point(point))
            for point in endpoints
        )
        record = _record(
            scene=scene,
            source=source,
            object_type="road",
            geometry=part,
            geometry_status=status,
            part_id=part_id,
            area=None,
            length=float(part.length),
            part_order=part_order,
            parent_way_id=str(source["parent_way_id"]),
            is_scene_boundary_endpoint=created_boundary_endpoint,
        )
        records.append(record)
    return tuple(records)


def _observe_poi(
    scene: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[ObservationRecord, ...]:
    geometry = source["geometry"]
    _validate_geometry(geometry, {"Point"}, label="POI source")
    if not scene["geometry"].covers(geometry):
        return ()
    return (
        _record(
            scene=scene,
            source=source,
            object_type="poi",
            geometry=geometry,
            geometry_status="full",
            part_id=None,
            area=None,
            length=None,
        ),
    )


def _observation_sort_key(
    record: ObservationRecord,
) -> tuple[str, str, str, int, str, int, str, str]:
    return (
        record.split,
        record.district_id,
        record.scene_id,
        OBJECT_ORDER[record.object_type],
        record.object_id,
        -1 if record.part_order is None else record.part_order,
        record.part_id or "",
        record.observation_id,
    )


def _materialize(
    scenes: Iterable[Mapping[str, Any]],
    objects: Iterable[Mapping[str, Any]],
) -> tuple[ObservationRecord, ...]:
    scene_rows = tuple(scenes)
    object_rows = tuple(objects)
    result: list[ObservationRecord] = []
    for scene in scene_rows:
        _validate_geometry(scene["geometry"], {"Polygon"}, label="scene")
        for source in object_rows:
            object_type = str(source["object_type"])
            if object_type == "building":
                result.extend(_observe_building(scene, source))
            elif object_type == "road":
                result.extend(_observe_road(scene, source))
            elif object_type == "poi":
                result.extend(_observe_poi(scene, source))
            else:
                raise ObservationContractError(
                    f"unsupported fixture object_type: {object_type}"
                )
    return tuple(sorted(result, key=_observation_sort_key))


def _content_hash(records: Iterable[ObservationRecord]) -> str:
    payload = json.dumps(
        [asdict(record) for record in records],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_fixture(path: Path) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ObservationContractError(
            f"cannot load observation fixture {path}: {exc}"
        ) from exc
    root = _mapping(data, "fixture")
    if root.get("crs") != "EPSG:5186":
        raise ObservationContractError("fixture CRS must be EPSG:5186")
    return root


def _parse_scenes(root: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    value = root.get("scenes")
    if not isinstance(value, list):
        raise ObservationContractError("fixture scenes must be a list")
    scenes: list[dict[str, Any]] = []
    for raw in value:
        spec = dict(_mapping(raw, "fixture scene"))
        spec["geometry"] = from_wkt(str(spec.pop("wkt")))
        scenes.append(spec)
    return tuple(scenes)


def _parse_objects(
    root: Mapping[str, Any],
    key: str = "objects",
) -> tuple[dict[str, Any], ...]:
    value = root.get(key)
    if not isinstance(value, list):
        raise ObservationContractError(f"fixture {key} must be a list")
    objects: list[dict[str, Any]] = []
    for raw in value:
        spec = dict(_mapping(raw, f"fixture {key} object"))
        spec["geometry"] = from_wkt(str(spec.pop("wkt")))
        objects.append(spec)
    return tuple(objects)


def validate_fixture(
    schema: ObservationSchema,
    fixture_path: str | Path,
) -> FixtureValidationResult:
    """Validate expected synthetic outputs without reading project GIS data."""

    path = Path(fixture_path).expanduser().resolve()
    root = _load_fixture(path)
    if root.get("schema_version") != schema.schema_version:
        raise ObservationContractError(
            "fixture and observation schema versions differ"
        )
    scenes = _parse_scenes(root)
    objects = _parse_objects(root)
    records = _materialize(scenes, objects)
    regenerated = _materialize(reversed(scenes), reversed(objects))
    deterministic = records == regenerated

    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        counts[record.scene_id][record.object_type] += 1
    normalized_counts = {
        scene_id: {
            object_type: counts[scene_id].get(object_type, 0)
            for object_type in EXPECTED_OBJECT_TYPE_ORDER
        }
        for scene_id in sorted(counts)
    }

    expected = _mapping(root.get("expected"), "fixture expected")
    expected_counts = _mapping(
        expected.get("count_by_scene_and_type"),
        "fixture expected counts",
    )
    count_match = normalized_counts == {
        str(scene_id): {
            str(object_type): int(count)
            for object_type, count in _mapping(
                scene_counts,
                "expected scene counts",
            ).items()
        }
        for scene_id, scene_counts in expected_counts.items()
    }

    overlap_spec = _mapping(
        expected.get("overlapping_object"),
        "expected overlapping object",
    )
    overlap_ids = {
        record.observation_id
        for record in records
        if record.object_id == overlap_spec.get("object_id")
        and record.scene_id in set(overlap_spec.get("scene_ids", []))
    }
    overlap_pass = len(overlap_ids) == len(overlap_spec.get("scene_ids", []))

    raster_states = _mapping(root.get("raster_states"), "raster_states")
    raster_nodata_distinct = (
        raster_states.get("landcover") == "raster_nodata"
        and raster_states.get("poi_geometry_embedding") == "not_applicable"
        and raster_states.get("source_missing") == "missing"
        and raster_states.get("boolean_false") == "observed_false"
        and len(set(raster_states.values())) == 4
    )

    invalid_count = 0
    collection_count = 0
    for source in _parse_objects(root, "hard_failure_objects"):
        try:
            _materialize((scenes[0],), (source,))
        except ObservationGeometryError as exc:
            if "invalid" in str(exc):
                invalid_count += 1
            if "GeometryCollection" in str(exc):
                collection_count += 1

    result_hash = _content_hash(records)
    expected_output_match = (
        count_match
        and overlap_pass
        and invalid_count
        == int(expected.get("invalid_geometry_hard_failures", -1))
        and collection_count
        == int(expected.get("geometry_collection_hard_failures", -1))
        and raster_nodata_distinct
        and result_hash == expected.get("content_hash")
    )
    valid = deterministic and expected_output_match
    return FixtureValidationResult(
        valid=valid,
        fixture_name=str(root.get("fixture_name")),
        observation_count=len(records),
        count_by_scene_and_type=normalized_counts,
        deterministic_regeneration=deterministic,
        expected_output_match=expected_output_match,
        invalid_geometry_hard_failures=invalid_count,
        geometry_collection_hard_failures=collection_count,
        overlapping_object_distinct_observation_ids=overlap_pass,
        raster_nodata_distinct=raster_nodata_distinct,
        content_hash=result_hash,
        source_access=False,
        records=records,
    )


EXPECTED_OBJECT_TYPE_ORDER = ("building", "road", "poi")
