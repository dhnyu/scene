"""D-012 polyline segment arc-length Fourier primitive."""

from __future__ import annotations

import math

import torch


def _complex_dtype(dtype: torch.dtype) -> torch.dtype:
    return torch.complex128 if dtype == torch.float64 else torch.complex64


def _zero_complex_like(omega: torch.Tensor, dtype: torch.dtype) -> torch.Tensor:
    return torch.zeros(omega.shape[0], dtype=_complex_dtype(dtype), device=omega.device)


def segment_fourier_transform(
    segment: torch.Tensor,
    omega: torch.Tensor,
    *,
    denominator_epsilon: float | None = None,
) -> torch.Tensor:
    """Return arc-length integral for one finite segment.

    `denominator_epsilon` is accepted for D-003 interface symmetry. The segment
    formula uses `torch.sinc`, so it does not divide by the near-zero phase
    denominator directly.
    """

    del denominator_epsilon
    if segment.shape != (2, 2):
        raise ValueError("segment must have shape [2,2]")
    if omega.ndim != 2 or omega.shape[1] != 2:
        raise ValueError("omega must have shape [K,2]")
    dtype = torch.promote_types(segment.dtype, omega.dtype)
    if dtype not in (torch.float32, torch.float64):
        dtype = torch.float32
    segment = segment.to(device=omega.device, dtype=dtype)
    omega = omega.to(dtype=dtype)
    if not bool(torch.isfinite(segment).all()) or not bool(torch.isfinite(omega).all()):
        raise ValueError("segment and omega must be finite")

    direction = segment[1] - segment[0]
    length = torch.linalg.norm(direction)
    if float(length.item()) == 0.0:
        return _zero_complex_like(omega, dtype)

    midpoint = (segment[0] + segment[1]) * 0.5
    alpha = omega @ direction
    phase = -2.0 * math.pi * (omega @ midpoint)
    complex_phase = torch.exp(1j * phase.to(dtype=dtype))
    result = length * complex_phase * torch.sinc(alpha)
    if not bool(torch.isfinite(result.real).all() and torch.isfinite(result.imag).all()):
        raise ValueError("segment Fourier output is nonfinite")
    return result.to(dtype=_complex_dtype(dtype))
