"""D-001 relative-position encoding."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import torch
from torch import nn

D001_K = 16
D001_LAMBDA_MIN_M = 10.0
D001_LAMBDA_MAX_M = 1000.0
D001_TOLERANCE_M = 1.0e-9
D001_RELATIVE_CODE_DIM = 64
D001_RELATIVE_EMBEDDING_DIM = 64

D001_EXPECTED_WAVELENGTHS_M = (
    10.0000000000,
    13.5935639088,
    18.4784979742,
    25.1188643151,
    34.1454887383,
    46.4158883361,
    63.0957344480,
    85.7695898591,
    116.5914401180,
    158.4893192461,
    215.4434690032,
    292.8644564625,
    398.1071705535,
    541.1695265465,
    735.6422544596,
    1000.0000000000,
)


@dataclass(frozen=True, slots=True)
class RelativePositionOutput:
    """Relative-position module output tensors."""

    relative_xy_m: torch.Tensor
    relative_code: torch.Tensor
    relative_wavelength_mask: torch.Tensor
    e_rel: torch.Tensor

    def to_dict(self) -> dict[str, torch.Tensor]:
        return {
            "relative_xy_m": self.relative_xy_m,
            "relative_code": self.relative_code,
            "relative_wavelength_mask": self.relative_wavelength_mask,
            "e_rel": self.e_rel,
        }


def generate_relative_wavelengths(
    *,
    device: torch.device | str | None = None,
    dtype: torch.dtype = torch.float64,
) -> torch.Tensor:
    """Generate the approved D-001 wavelengths in declared order."""

    if dtype not in (torch.float64, torch.float32):
        raise ValueError("relative wavelengths support only float64 or float32")
    ratio = D001_LAMBDA_MAX_M / D001_LAMBDA_MIN_M
    values = [
        D001_LAMBDA_MIN_M * (ratio ** (k / (D001_K - 1)))
        for k in range(D001_K)
    ]
    return torch.tensor(values, dtype=dtype, device=device)


def validate_relative_wavelengths(wavelengths: torch.Tensor) -> dict[str, object]:
    """Validate D-001 wavelength contract without sorting or mutation."""

    if wavelengths.ndim != 1:
        raise ValueError("relative wavelengths must be rank-1")
    reference = generate_relative_wavelengths(
        device=wavelengths.device,
        dtype=torch.float64,
    )
    observed = wavelengths.to(dtype=torch.float64)
    expected = torch.tensor(
        D001_EXPECTED_WAVELENGTHS_M,
        dtype=torch.float64,
        device=wavelengths.device,
    )
    diffs = torch.abs(observed - expected)
    generated_diffs = torch.abs(reference - expected)
    checks = {
        "k": int(observed.numel()),
        "k_is_16": observed.numel() == D001_K,
        "first_is_10": bool(torch.isclose(observed[0], torch.tensor(10.0, device=wavelengths.device, dtype=torch.float64), atol=D001_TOLERANCE_M, rtol=0.0)) if observed.numel() else False,
        "last_is_1000": bool(torch.isclose(observed[-1], torch.tensor(1000.0, device=wavelengths.device, dtype=torch.float64), atol=D001_TOLERANCE_M, rtol=0.0)) if observed.numel() else False,
        "strictly_increasing": bool(torch.all(observed[1:] > observed[:-1])) if observed.numel() > 1 else False,
        "duplicate_count": int(observed.numel() - torch.unique(observed).numel()),
        "all_finite": bool(torch.isfinite(observed).all()),
        "all_positive": bool((observed > 0).all()),
        "max_abs_diff_expected": float(diffs.max().item()) if diffs.numel() else math.inf,
        "max_abs_diff_generated": float(generated_diffs.max().item()) if generated_diffs.numel() else math.inf,
        "dtype": str(wavelengths.dtype).replace("torch.", ""),
    }
    checks["valid"] = (
        checks["k_is_16"]
        and checks["first_is_10"]
        and checks["last_is_1000"]
        and checks["strictly_increasing"]
        and checks["duplicate_count"] == 0
        and checks["all_finite"]
        and checks["all_positive"]
        and checks["max_abs_diff_expected"] <= D001_TOLERANCE_M
    )
    return checks


def compute_relative_xy(
    representative_xy_m: torch.Tensor,
    scene_center_xy_m: torch.Tensor,
    object_mask: torch.Tensor | None = None,
) -> torch.Tensor:
    """Compute relative EPSG:5186 metre coordinates from absolute coordinates."""

    if representative_xy_m.ndim != 3 or representative_xy_m.shape[-1] != 2:
        raise ValueError("representative_xy_m must have shape [B,N,2]")
    if scene_center_xy_m.ndim != 2 or scene_center_xy_m.shape[-1] != 2:
        raise ValueError("scene_center_xy_m must have shape [B,2]")
    if representative_xy_m.shape[0] != scene_center_xy_m.shape[0]:
        raise ValueError("batch dimensions must match")
    relative_xy_m = representative_xy_m.to(dtype=torch.float32) - scene_center_xy_m.to(
        dtype=torch.float32,
        device=representative_xy_m.device,
    )[:, None, :]
    if object_mask is not None:
        relative_xy_m = apply_object_mask(relative_xy_m, object_mask)
    return relative_xy_m


def apply_object_mask(values: torch.Tensor, object_mask: torch.Tensor) -> torch.Tensor:
    """Zero tensor values where the object mask is false."""

    if object_mask.ndim != 2:
        raise ValueError("object_mask must have shape [B,N]")
    if values.shape[:2] != object_mask.shape:
        raise ValueError("value and object mask batch/object dimensions must match")
    return values * object_mask.to(device=values.device, dtype=values.dtype).unsqueeze(-1)


def encode_relative_position(
    relative_xy_m: torch.Tensor,
    *,
    object_mask: torch.Tensor | None = None,
    wavelengths_m: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Encode relative metre coordinates with D-001 sinusoidal features."""

    if relative_xy_m.ndim != 3 or relative_xy_m.shape[-1] != 2:
        raise ValueError("relative_xy_m must have shape [B,N,2]")
    device = relative_xy_m.device
    wavelengths = wavelengths_m
    if wavelengths is None:
        wavelengths = generate_relative_wavelengths(device=device, dtype=torch.float64)
    if wavelengths.ndim != 1 or wavelengths.numel() != D001_K:
        raise ValueError("wavelengths_m must have shape [16]")
    validation = validate_relative_wavelengths(wavelengths.to(device=device))
    if not validation["valid"]:
        raise ValueError("wavelengths_m does not satisfy D-001")

    xy = relative_xy_m.to(dtype=torch.float32)
    angles = (2.0 * math.pi) * xy.unsqueeze(-2) / wavelengths.to(
        device=device,
        dtype=torch.float32,
    ).view(1, 1, D001_K, 1)
    px = angles[..., 0]
    py = angles[..., 1]
    code = torch.stack(
        (torch.sin(px), torch.cos(px), torch.sin(py), torch.cos(py)),
        dim=-1,
    ).reshape(*relative_xy_m.shape[:2], D001_RELATIVE_CODE_DIM)

    if object_mask is None:
        wavelength_mask = torch.ones(
            (*relative_xy_m.shape[:2], D001_K),
            dtype=torch.bool,
            device=device,
        )
    else:
        wavelength_mask = object_mask.to(device=device, dtype=torch.bool).unsqueeze(-1).expand(
            -1,
            -1,
            D001_K,
        )
        code = apply_object_mask(code, object_mask)

    return code.to(dtype=torch.float32), wavelength_mask


class RelativePositionEncoder(nn.Module):
    """D-001 `f_rel`: 64 -> 128 -> 64 MLP."""

    def __init__(self, dropout: float = 0.1) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(D001_RELATIVE_CODE_DIM, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(128, D001_RELATIVE_EMBEDDING_DIM),
        )

    def forward(
        self,
        relative_code: torch.Tensor,
        object_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if relative_code.ndim != 3 or relative_code.shape[-1] != D001_RELATIVE_CODE_DIM:
            raise ValueError("relative_code must have shape [B,N,64]")
        e_rel = self.network(relative_code.to(dtype=torch.float32))
        if object_mask is not None:
            e_rel = apply_object_mask(e_rel, object_mask)
        return e_rel


class RelativePositionModule(nn.Module):
    """Full relative-position path from relative_xy_m to relative_code and e_rel."""

    def __init__(self, dropout: float = 0.1) -> None:
        super().__init__()
        wavelengths = generate_relative_wavelengths(dtype=torch.float64)
        self.register_buffer("wavelengths_m", wavelengths, persistent=False)
        self.encoder = RelativePositionEncoder(dropout=dropout)

    def forward(
        self,
        relative_xy_m: torch.Tensor,
        object_mask: torch.Tensor | None = None,
    ) -> RelativePositionOutput:
        if object_mask is None:
            object_mask = torch.ones(
                relative_xy_m.shape[:2],
                dtype=torch.bool,
                device=relative_xy_m.device,
            )
        relative_xy_masked = apply_object_mask(
            relative_xy_m.to(dtype=torch.float32),
            object_mask,
        )
        relative_code, wavelength_mask = encode_relative_position(
            relative_xy_masked,
            object_mask=object_mask,
            wavelengths_m=self.wavelengths_m.to(device=relative_xy_m.device),
        )
        e_rel = self.encoder(relative_code, object_mask=object_mask)
        return RelativePositionOutput(
            relative_xy_m=relative_xy_masked,
            relative_code=relative_code,
            relative_wavelength_mask=wavelength_mask,
            e_rel=e_rel,
        )

    def from_absolute(
        self,
        representative_xy_m: torch.Tensor,
        scene_center_xy_m: torch.Tensor,
        object_mask: torch.Tensor | None = None,
    ) -> RelativePositionOutput:
        relative_xy = compute_relative_xy(
            representative_xy_m,
            scene_center_xy_m,
            object_mask=object_mask,
        )
        return self.forward(relative_xy, object_mask=object_mask)


def relative_architecture_metadata() -> dict[str, object]:
    """Return deterministic M4.2 architecture metadata for stage manifests."""

    return {
        "stage_id": "M4.2",
        "contract": "D-001",
        "relative_xy_m": "[B,N,2] float32",
        "relative_code": "[B,N,64] float32",
        "relative_wavelength_mask": "[B,N,16] bool",
        "e_rel": "[B,N,64] float32",
        "wavelength_count": D001_K,
        "lambda_min_m": D001_LAMBDA_MIN_M,
        "lambda_max_m": D001_LAMBDA_MAX_M,
        "component_order": "[sin(px), cos(px), sin(py), cos(py)]",
        "mlp": [
            "Linear 64 -> 128",
            "LayerNorm(128)",
            "GELU",
            "Dropout(0.1)",
            "Linear 128 -> 64",
        ],
        "forbidden": [
            "absolute-coordinate neural feature",
            "runtime wavelength sorting",
            "M4.3 geometry Fourier",
            "production feature materialization",
            "trained checkpoint",
        ],
    }
