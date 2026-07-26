"""Geometry Fourier primitive module for M4.3."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from scene.m4.geometry_frequency import generate_frequency_grid
from scene.m4.polygon_fourier import polygon_fourier_transform
from scene.m4.polyline_fourier import polyline_fourier_transform


@dataclass(frozen=True, slots=True)
class GeometryPrimitiveOutput:
    """Geometry primitive batch output."""

    fourier_complex: torch.Tensor
    geometry_frequency_mask: torch.Tensor


class GeometryFourierPrimitive:
    """Project-owned D-002/D-003/D-012/D-013 primitive wrapper."""

    def __init__(self, *, device: torch.device | str | None = None) -> None:
        self.device = torch.device(device or "cpu")
        self.omega = generate_frequency_grid(device=self.device, dtype=torch.float32)

    def encode_one(
        self,
        geometry: Polygon | MultiPolygon | LineString | MultiLineString,
        *,
        object_type: str,
    ) -> torch.Tensor:
        if object_type == "building":
            return polygon_fourier_transform(geometry, self.omega, dtype=torch.float32)
        if object_type == "road":
            return polyline_fourier_transform(geometry, self.omega, dtype=torch.float32)
        raise ValueError("geometry primitive supports only building and road")

    def encode_batch(
        self,
        geometries: list[list[Polygon | MultiPolygon | LineString | MultiLineString | None]],
        object_types: list[list[str | None]],
        object_mask: torch.Tensor,
    ) -> GeometryPrimitiveOutput:
        if object_mask.ndim != 2:
            raise ValueError("object_mask must have shape [B,N]")
        batch_size, object_count = object_mask.shape
        output = torch.zeros(
            (batch_size, object_count, 128),
            dtype=torch.complex64,
            device=self.device,
        )
        frequency_mask = torch.zeros(
            (batch_size, object_count, 128),
            dtype=torch.bool,
            device=self.device,
        )
        for b in range(batch_size):
            for n in range(object_count):
                if not bool(object_mask[b, n]):
                    continue
                geometry = geometries[b][n]
                object_type = object_types[b][n]
                if geometry is None or object_type is None:
                    raise ValueError("valid object requires geometry and object_type")
                output[b, n] = self.encode_one(geometry, object_type=object_type)
                frequency_mask[b, n] = True
        return GeometryPrimitiveOutput(
            fourier_complex=output,
            geometry_frequency_mask=frequency_mask,
        )


def geometry_primitive_metadata() -> dict[str, object]:
    """Return deterministic M4.3 primitive metadata."""

    return {
        "stage_id": "M4.3",
        "contracts": ["D-002", "D-003", "D-012", "D-013"],
        "omega": "[128,2] float32, radius-major angle-minor",
        "triangle": "area integral primitive",
        "segment": "arc-length integral primitive",
        "polygon": "triangulation then triangle sum; holes excluded",
        "polyline": "segment decomposition then segment sum",
        "fourier_complex": "[B,N,128] complex64",
        "excluded": [
            "magnitude",
            "phase",
            "x_mag",
            "x_phase",
            "f_mag",
            "f_phase",
            "f_geom",
            "e_geom",
            "production feature artifact",
        ],
    }
