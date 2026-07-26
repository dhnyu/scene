"""LineString and MultiLineString Fourier aggregation."""

from __future__ import annotations

import torch
from shapely.geometry import LineString, MultiLineString

from scene.m4.polygon_fourier import GeometryPrimitiveError
from scene.m4.segment_fourier import _complex_dtype, segment_fourier_transform


def _line_components(geometry: LineString | MultiLineString) -> list[LineString]:
    if geometry.is_empty:
        raise GeometryPrimitiveError("line geometry is empty")
    if not geometry.is_valid:
        raise GeometryPrimitiveError("line geometry is invalid; repair is forbidden")
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return sorted(list(geometry.geoms), key=lambda g: g.wkb)
    raise GeometryPrimitiveError(f"unexpected line geometry type: {geometry.geom_type}")


def decompose_line_segments(geometry: LineString | MultiLineString) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return finite nonzero line segments in deterministic order."""

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for line in _line_components(geometry):
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        for first, second in zip(coords[:-1], coords[1:]):
            a = (float(first[0]), float(first[1]))
            b = (float(second[0]), float(second[1]))
            if a == b:
                continue
            segments.append((a, b))
    if not segments:
        raise GeometryPrimitiveError("line decomposition produced no finite segments")
    return sorted(segments, key=lambda pair: (min(pair), max(pair)))


def polyline_fourier_transform(
    geometry: LineString | MultiLineString,
    omega: torch.Tensor,
    *,
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """Return polyline arc-length Fourier transform as a sum of segments."""

    total = torch.zeros(omega.shape[0], dtype=_complex_dtype(dtype), device=omega.device)
    for segment in decompose_line_segments(geometry):
        tensor = torch.tensor(segment, dtype=dtype, device=omega.device)
        total = total + segment_fourier_transform(tensor, omega.to(dtype=dtype))
    if not bool(torch.isfinite(total.real).all() and torch.isfinite(total.imag).all()):
        raise GeometryPrimitiveError("polyline Fourier output is nonfinite")
    return total.to(dtype=_complex_dtype(dtype))
