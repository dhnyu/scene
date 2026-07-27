"""M4 geometry feature and encoder modules.

This module implements the D-002/D-003 geometry feature contract and the
study-methods geometry encoder architecture. It does not train the encoder.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

import numpy as np
import torch
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from torch import nn

from scene.m4.geometry_module import GeometryFourierPrimitive
from scene.m4.polygon_errors import GeometryPrimitiveError

GEOMETRY_SCENE_WINDOW_M = 500.0
GEOMETRY_PHASE_ZERO_EPSILON = 1.0e-7
GEOMETRY_ENCODER_SEED = 20260727
GEOMETRY_EMBEDDING_DIM = 128
GEOMETRY_K_F = 128


@dataclass(frozen=True, slots=True)
class GeometryFeatureOutput:
    """Magnitude and phase features derived from complex Fourier coefficients."""

    fourier_magnitude: torch.Tensor
    fourier_phase: torch.Tensor
    x_mag: torch.Tensor
    x_phase: torch.Tensor


@dataclass(frozen=True, slots=True)
class GeometryEncoderOutput:
    """Geometry encoder output tensors."""

    e_mag: torch.Tensor
    e_phase: torch.Tensor
    e_geom: torch.Tensor


def intrinsic_geometry(
    geometry: Polygon | MultiPolygon | LineString | MultiLineString,
    *,
    representative_x: float,
    representative_y: float,
    scene_window_m: float = GEOMETRY_SCENE_WINDOW_M,
) -> Polygon | MultiPolygon | LineString | MultiLineString:
    """Return object-centered intrinsic geometry without topology repair."""

    if not np.isfinite([representative_x, representative_y, scene_window_m]).all():
        raise GeometryPrimitiveError("representative point or scene window is nonfinite")
    if scene_window_m <= 0.0:
        raise GeometryPrimitiveError("scene window length must be positive")
    shifted = affinity.translate(
        geometry,
        xoff=-float(representative_x),
        yoff=-float(representative_y),
    )
    return affinity.scale(
        shifted,
        xfact=1.0 / float(scene_window_m),
        yfact=1.0 / float(scene_window_m),
        origin=(0.0, 0.0),
    )


def fourier_to_magnitude_phase(
    fourier_complex: torch.Tensor,
    *,
    phase_zero_epsilon: float = GEOMETRY_PHASE_ZERO_EPSILON,
) -> GeometryFeatureOutput:
    """Convert complex Fourier coefficients to contract-defined features."""

    if not torch.is_complex(fourier_complex):
        raise TypeError("fourier_complex must be a complex tensor")
    real = fourier_complex.real.to(dtype=torch.float32)
    imag = fourier_complex.imag.to(dtype=torch.float32)
    magnitude = torch.hypot(real, imag)
    phase = torch.zeros_like(magnitude)
    active = magnitude > float(phase_zero_epsilon)
    phase = torch.where(active, torch.atan2(imag, real), phase)
    x_mag = torch.log1p(magnitude)
    x_phase = torch.cat((torch.cos(phase), torch.sin(phase)), dim=-1)
    return GeometryFeatureOutput(
        fourier_magnitude=magnitude,
        fourier_phase=phase,
        x_mag=x_mag,
        x_phase=x_phase,
    )


def _feed_forward(input_dim: int, hidden_dim: int, output_dim: int, *, final_norm: bool = False) -> nn.Sequential:
    layers: list[nn.Module] = [
        nn.Linear(input_dim, hidden_dim),
        nn.LayerNorm(hidden_dim),
        nn.GELU(),
        nn.Dropout(0.1),
        nn.Linear(hidden_dim, output_dim),
    ]
    if final_norm:
        layers.append(nn.LayerNorm(output_dim))
    return nn.Sequential(*layers)


class GeometryEncoder(nn.Module):
    """Study-methods M4 geometry encoder in initialized, untrained form."""

    def __init__(self) -> None:
        super().__init__()
        self.f_mag = _feed_forward(128, 256, 128)
        self.f_phase = _feed_forward(256, 256, 128)
        self.f_geom = _feed_forward(256, 256, 128, final_norm=True)

    def forward(self, x_mag: torch.Tensor, x_phase: torch.Tensor) -> GeometryEncoderOutput:
        if x_mag.shape[-1] != 128:
            raise ValueError("x_mag last dimension must be 128")
        if x_phase.shape[-1] != 256:
            raise ValueError("x_phase last dimension must be 256")
        e_mag = self.f_mag(x_mag)
        e_phase = self.f_phase(x_phase)
        e_geom = self.f_geom(torch.cat((e_mag, e_phase), dim=-1))
        return GeometryEncoderOutput(e_mag=e_mag, e_phase=e_phase, e_geom=e_geom)


def initialize_geometry_encoder(
    *,
    seed: int = GEOMETRY_ENCODER_SEED,
    device: torch.device | str | None = None,
) -> GeometryEncoder:
    """Initialize one deterministic untrained geometry encoder."""

    torch.manual_seed(int(seed))
    model = GeometryEncoder()
    model.to(device=torch.device(device or "cpu"), dtype=torch.float32)
    model.eval()
    return model


def geometry_architecture_metadata(*, seed: int = GEOMETRY_ENCODER_SEED) -> dict[str, Any]:
    """Return deterministic architecture metadata for manifests and reports."""

    return {
        "encoder_status": "initialized_untrained",
        "initialization_seed": int(seed),
        "initializer": "PyTorch default parameter initialization under torch.manual_seed",
        "dtype": "float32",
        "fourier_complex_dtype": "complex64",
        "phase_zero_epsilon": GEOMETRY_PHASE_ZERO_EPSILON,
        "scene_window_m": GEOMETRY_SCENE_WINDOW_M,
        "f_mag": ["Linear 128->256", "LayerNorm(256)", "GELU", "Dropout(0.1)", "Linear 256->128"],
        "f_phase": ["Linear 256->256", "LayerNorm(256)", "GELU", "Dropout(0.1)", "Linear 256->128"],
        "f_geom": [
            "Linear 256->256",
            "LayerNorm(256)",
            "GELU",
            "Dropout(0.1)",
            "Linear 256->128",
            "LayerNorm(128)",
        ],
        "e_geom_dim": GEOMETRY_EMBEDDING_DIM,
    }


def state_dict_sha256(model: nn.Module) -> str:
    """Hash a state_dict independent of device placement."""

    digest = hashlib.sha256()
    state = model.state_dict()
    for name in sorted(state):
        tensor = state[name].detach().cpu().contiguous()
        array = tensor.numpy()
        digest.update(name.encode("utf-8"))
        digest.update(str(array.dtype).encode("utf-8"))
        digest.update(json.dumps(list(array.shape), separators=(",", ":")).encode("utf-8"))
        digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def encode_observation_geometry(
    geometry: Polygon | MultiPolygon | LineString | MultiLineString,
    *,
    object_type: str,
    representative_x: float,
    representative_y: float,
    primitive: GeometryFourierPrimitive | None = None,
) -> torch.Tensor:
    """Return intrinsic complex Fourier coefficients for one building/road."""

    primitive = primitive or GeometryFourierPrimitive(device="cpu")
    intrinsic = intrinsic_geometry(
        geometry,
        representative_x=representative_x,
        representative_y=representative_y,
    )
    return primitive.encode_one(intrinsic, object_type=object_type)

