"""Polygon and MultiPolygon Fourier aggregation."""

from __future__ import annotations

import torch
from shapely.geometry import MultiPolygon, Polygon

from scene.m4.polygon_errors import GeometryPrimitiveError
from scene.m4.segment_fourier import _complex_dtype
from scene.m4.triangle_fourier import triangle_fourier_transform
from scene.m4.triangle_backend import triangulate_polygon_domain


def _ensure_polygon_valid(geometry: Polygon | MultiPolygon) -> None:
    if geometry.is_empty:
        raise GeometryPrimitiveError("geometry is empty")
    if not geometry.is_valid:
        raise GeometryPrimitiveError("geometry is invalid; repair is forbidden")


def polygon_fourier_transform(
    geometry: Polygon | MultiPolygon,
    omega: torch.Tensor,
    *,
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """Return polygon domain Fourier transform as a sum of triangle transforms."""

    triangles = triangulate_polygon_domain(geometry)
    total = torch.zeros(omega.shape[0], dtype=_complex_dtype(dtype), device=omega.device)
    for triangle in triangles:
        coords = list(triangle.exterior.coords)[:3]
        tensor = torch.tensor(coords, dtype=dtype, device=omega.device)
        total = total + triangle_fourier_transform(tensor, omega.to(dtype=dtype))
    if not bool(torch.isfinite(total.real).all() and torch.isfinite(total.imag).all()):
        raise GeometryPrimitiveError("polygon Fourier output is nonfinite")
    return total.to(dtype=_complex_dtype(dtype))
