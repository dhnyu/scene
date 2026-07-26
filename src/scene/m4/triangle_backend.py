"""Constrained polygon triangulation backend for M4 geometry primitives."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon

from scene.m4.polygon_errors import GeometryPrimitiveError

TRIANGLE_OPTIONS = "pYq"
AREA_EPSILON_M2 = 1.0e-6


@dataclass(frozen=True)
class TriangleBackendInfo:
    """Runtime dependency status for the Triangle backend."""

    import_ok: bool
    version: str | None
    options: str
    module_path: str | None
    error: str | None = None


@dataclass(frozen=True)
class TriangleMesh:
    """Triangulated polygon component with provenance metadata."""

    component_index: int
    triangles: tuple[Polygon, ...]
    triangle_count: int
    zero_area_triangle_count: int


def triangle_dependency_info() -> TriangleBackendInfo:
    """Return dependency/version information without mutating state."""

    try:
        triangle = import_module("triangle")
    except Exception as exc:  # pragma: no cover - exact import failure varies.
        return TriangleBackendInfo(
            import_ok=False,
            version=None,
            options=TRIANGLE_OPTIONS,
            module_path=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    return TriangleBackendInfo(
        import_ok=True,
        version=str(getattr(triangle, "__version__", "UNKNOWN")),
        options=TRIANGLE_OPTIONS,
        module_path=str(getattr(triangle, "__file__", "")),
    )


def _triangle_module() -> Any:
    info = triangle_dependency_info()
    if not info.import_ok:
        raise GeometryPrimitiveError(
            "triangle backend is unavailable; official polygon triangulation "
            f"requires triangle.triangulate ({info.error})"
        )
    return import_module("triangle")


def _ring_coordinates(ring: Any, *, ring_name: str) -> np.ndarray:
    coords = np.asarray(ring.coords, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise GeometryPrimitiveError(f"{ring_name} ring must be two-dimensional")
    if len(coords) < 4:
        raise GeometryPrimitiveError(f"{ring_name} ring has fewer than 4 closed coordinates")
    if np.array_equal(coords[0], coords[-1]):
        coords = coords[:-1]
    if len(coords) < 3:
        raise GeometryPrimitiveError(f"{ring_name} ring has fewer than 3 unique vertices")
    if not np.isfinite(coords).all():
        raise GeometryPrimitiveError(f"{ring_name} ring has nonfinite coordinate")
    return coords


def _hole_seed(interior_ring: Any, *, component_index: int, hole_index: int) -> tuple[float, float]:
    coords = _ring_coordinates(
        interior_ring,
        ring_name=f"component {component_index} hole {hole_index}",
    )
    hole_polygon = Polygon(coords)
    if hole_polygon.is_empty or not hole_polygon.is_valid or hole_polygon.area <= 0.0:
        raise GeometryPrimitiveError(
            f"component {component_index} hole {hole_index} is not a valid polygon"
        )
    seed = hole_polygon.representative_point()
    if not isinstance(seed, Point) or not hole_polygon.contains(seed):
        raise GeometryPrimitiveError(
            f"component {component_index} hole {hole_index} has no strict interior seed"
        )
    return (float(seed.x), float(seed.y))


def polygon_to_pslg(polygon: Polygon, *, component_index: int = 0) -> dict[str, np.ndarray]:
    """Convert a Polygon into a Triangle-compatible PSLG without repair."""

    if polygon.is_empty:
        raise GeometryPrimitiveError(f"component {component_index} is empty")
    if not polygon.is_valid:
        raise GeometryPrimitiveError(f"component {component_index} is invalid; repair is forbidden")

    vertex_blocks: list[np.ndarray] = []
    segment_blocks: list[np.ndarray] = []
    offset = 0

    exterior = _ring_coordinates(polygon.exterior, ring_name=f"component {component_index} exterior")
    vertex_blocks.append(exterior)
    n_ext = exterior.shape[0]
    segment_blocks.append(
        np.column_stack(
            (
                np.arange(offset, offset + n_ext, dtype=np.int64),
                np.r_[np.arange(offset + 1, offset + n_ext, dtype=np.int64), offset],
            )
        )
    )
    offset += n_ext

    holes: list[tuple[float, float]] = []
    for hole_index, interior in enumerate(polygon.interiors):
        hole_coords = _ring_coordinates(
            interior,
            ring_name=f"component {component_index} hole {hole_index}",
        )
        vertex_blocks.append(hole_coords)
        n_hole = hole_coords.shape[0]
        segment_blocks.append(
            np.column_stack(
                (
                    np.arange(offset, offset + n_hole, dtype=np.int64),
                    np.r_[np.arange(offset + 1, offset + n_hole, dtype=np.int64), offset],
                )
            )
        )
        holes.append(_hole_seed(interior, component_index=component_index, hole_index=hole_index))
        offset += n_hole

    payload: dict[str, np.ndarray] = {
        "vertices": np.vstack(vertex_blocks).astype(np.float64, copy=False),
        "segments": np.vstack(segment_blocks).astype(np.int32, copy=False),
    }
    if holes:
        payload["holes"] = np.asarray(holes, dtype=np.float64)
    return payload


def polygon_components(geometry: Polygon | MultiPolygon) -> list[Polygon]:
    """Return deterministic Polygon components for Polygon or MultiPolygon input."""

    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return sorted(list(geometry.geoms), key=lambda g: g.wkb)
    raise GeometryPrimitiveError(f"unexpected polygon geometry type: {geometry.geom_type}")


def triangulate_polygon_component(
    polygon: Polygon,
    *,
    component_index: int = 0,
    options: str = TRIANGLE_OPTIONS,
) -> TriangleMesh:
    """Triangulate one polygon component using triangle.triangulate."""

    triangle = _triangle_module()
    pslg = polygon_to_pslg(polygon, component_index=component_index)
    mesh = triangle.triangulate(pslg, options)
    if "vertices" not in mesh or "triangles" not in mesh:
        raise GeometryPrimitiveError(f"component {component_index} triangulation returned no triangles")
    vertices = np.asarray(mesh["vertices"], dtype=np.float64)
    triangle_indices = np.asarray(mesh["triangles"], dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 2 or not np.isfinite(vertices).all():
        raise GeometryPrimitiveError(f"component {component_index} triangulation vertices are invalid")
    if triangle_indices.ndim != 2 or triangle_indices.shape[1] != 3:
        raise GeometryPrimitiveError(f"component {component_index} triangle index array is invalid")

    triangles: list[Polygon] = []
    zero_area = 0
    for row in triangle_indices:
        if np.any(row < 0) or np.any(row >= len(vertices)):
            raise GeometryPrimitiveError(f"component {component_index} triangle index out of range")
        triangle_polygon = Polygon(vertices[row])
        area = float(triangle_polygon.area)
        if not np.isfinite(area):
            raise GeometryPrimitiveError(f"component {component_index} triangle area is nonfinite")
        if area <= 0.0:
            zero_area += 1
            continue
        if not polygon.covers(triangle_polygon.representative_point()):
            raise GeometryPrimitiveError(f"component {component_index} triangle lies outside polygon domain")
        triangles.append(triangle_polygon)

    if not triangles:
        raise GeometryPrimitiveError(f"component {component_index} triangulation produced no positive-area triangles")
    return TriangleMesh(
        component_index=component_index,
        triangles=tuple(sorted(triangles, key=lambda g: g.wkb)),
        triangle_count=len(triangles),
        zero_area_triangle_count=zero_area,
    )


def triangulate_polygon_domain(
    geometry: Polygon | MultiPolygon,
    *,
    options: str = TRIANGLE_OPTIONS,
) -> list[Polygon]:
    """Triangulate a Polygon or MultiPolygon domain with constrained Triangle."""

    if geometry.is_empty:
        raise GeometryPrimitiveError("geometry is empty")
    if not geometry.is_valid:
        raise GeometryPrimitiveError("geometry is invalid; repair is forbidden")

    triangles: list[Polygon] = []
    for component_index, polygon in enumerate(polygon_components(geometry)):
        mesh = triangulate_polygon_component(
            polygon,
            component_index=component_index,
            options=options,
        )
        triangles.extend(mesh.triangles)
    if not triangles:
        raise GeometryPrimitiveError("polygon triangulation produced no domain triangles")
    return sorted(triangles, key=lambda g: g.wkb)
