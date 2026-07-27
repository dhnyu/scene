"""D-002 geometry Fourier frequency grid."""

from __future__ import annotations

import math

import torch

GEOMETRY_K_F = 128
GEOMETRY_N_RHO = 8
GEOMETRY_N_THETA = 16
GEOMETRY_RHO_MIN = 0.5
GEOMETRY_RHO_MAX = 50.0
GEOMETRY_RADIUS_VALUES = (
    0.5000000000,
    0.9653488644,
    1.8637968602,
    3.5984283650,
    6.9474774719,
    13.4134789764,
    25.8973733962,
    50.0000000000,
)


def generate_frequency_radii(
    *,
    device: torch.device | str | None = None,
    dtype: torch.dtype = torch.float64,
) -> torch.Tensor:
    """Return D-002 geometric radii in approved order."""

    values = [
        GEOMETRY_RHO_MIN
        * ((GEOMETRY_RHO_MAX / GEOMETRY_RHO_MIN) ** (q / (GEOMETRY_N_RHO - 1)))
        for q in range(GEOMETRY_N_RHO)
    ]
    return torch.tensor(values, dtype=dtype, device=device)


def generate_frequency_grid(
    *,
    device: torch.device | str | None = None,
    dtype: torch.dtype = torch.float64,
) -> torch.Tensor:
    """Return D-002 Omega as `[128,2]` radius-major, angle-minor."""

    values: list[tuple[float, float]] = []
    radii = [
        GEOMETRY_RHO_MIN
        * ((GEOMETRY_RHO_MAX / GEOMETRY_RHO_MIN) ** (q / (GEOMETRY_N_RHO - 1)))
        for q in range(GEOMETRY_N_RHO)
    ]
    for radius in radii:
        for m in range(GEOMETRY_N_THETA):
            angle = m * math.pi / GEOMETRY_N_THETA
            values.append((radius * math.cos(angle), radius * math.sin(angle)))
    return torch.tensor(values, dtype=dtype, device=device)


def validate_frequency_grid(omega: torch.Tensor) -> dict[str, object]:
    """Validate D-002 without sorting or mutating the frequency order."""

    expected = generate_frequency_grid(device=omega.device, dtype=torch.float64)
    observed = omega.to(dtype=torch.float64)
    norms = torch.linalg.norm(observed, dim=1) if observed.ndim == 2 else torch.tensor([])
    rounded = torch.round(observed * 1.0e10) / 1.0e10 if observed.ndim == 2 else observed
    duplicate_count = (
        int(observed.shape[0] - torch.unique(rounded, dim=0).shape[0])
        if observed.ndim == 2
        else -1
    )
    no_negative_pairs = True
    if observed.ndim == 2:
        pairs = {tuple(row.tolist()) for row in rounded.cpu()}
        no_negative_pairs = all(
            tuple((-row).tolist()) not in pairs
            for row in rounded.cpu()
        )
    max_abs_diff = (
        float(torch.abs(observed - expected).max().item())
        if observed.shape == expected.shape
        else math.inf
    )
    checks = {
        "shape": tuple(observed.shape),
        "shape_is_128x2": tuple(observed.shape) == (GEOMETRY_K_F, 2),
        "all_finite": bool(torch.isfinite(observed).all()) if observed.numel() else False,
        "omega_zero_count": int((norms == 0).sum().item()) if norms.numel() else -1,
        "duplicate_count": duplicate_count,
        "no_negative_pairs": no_negative_pairs,
        "max_abs_diff_expected": max_abs_diff,
    }
    checks["valid"] = (
        checks["shape_is_128x2"]
        and checks["all_finite"]
        and checks["omega_zero_count"] == 0
        and checks["duplicate_count"] == 0
        and checks["no_negative_pairs"]
        and checks["max_abs_diff_expected"] <= 1.0e-9
    )
    return checks
