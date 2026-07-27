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
    # Polygon domains sum many triangle area integrals. Some frequencies produce
    # very small coefficients where float32 CPU/CUDA phase can diverge after
    # atan2 even when complex values are close. Evaluate the analytic primitive
    # in float64 and cast the production tensor back to complex64.
    work_dtype = torch.float64 if dtype == torch.float32 else dtype
    work_omega = omega.to(dtype=work_dtype)
    total = torch.zeros(omega.shape[0], dtype=_complex_dtype(work_dtype), device=omega.device)
    for triangle in triangles:
        coords = list(triangle.exterior.coords)[:3]
        tensor = torch.tensor(coords, dtype=work_dtype, device=omega.device)
        total = total + triangle_fourier_transform(tensor, work_omega)
    if not bool(torch.isfinite(total.real).all() and torch.isfinite(total.imag).all()):
        raise GeometryPrimitiveError("polygon Fourier output is nonfinite")
    return total.to(dtype=_complex_dtype(dtype))
