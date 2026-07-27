"""D-012 polygon triangle area Fourier primitive."""

from __future__ import annotations

import math

import torch

from scene.m4.segment_fourier import _complex_dtype, _zero_complex_like


def triangle_signed_double_area(triangle: torch.Tensor) -> torch.Tensor:
    """Return signed double area for a `[3,2]` triangle."""

    return torch.linalg.det(
        torch.stack((triangle[1] - triangle[0], triangle[2] - triangle[0]), dim=1)
    )


def _phi(z: torch.Tensor, eps: float) -> torch.Tensor:
    """Return `(exp(z)-1)/z` with a series branch near zero."""

    result = torch.empty_like(z)
    small = torch.abs(z) <= eps
    if bool((~small).any()):
        idx = torch.where(~small)[0]
        result[idx] = torch.expm1(z[idx]) / z[idx]
    if bool(small.any()):
        idx = torch.where(small)[0]
        zi = z[idx]
        result[idx] = (
            1.0
            + zi / 2.0
            + zi**2 / 6.0
            + zi**3 / 24.0
            + zi**4 / 120.0
            + zi**5 / 720.0
        )
    return result


def _psi(z: torch.Tensor, eps: float) -> torch.Tensor:
    """Return `(exp(z)-1-z)/z^2` with a series branch near zero."""

    result = torch.empty_like(z)
    small = torch.abs(z) <= eps
    if bool((~small).any()):
        idx = torch.where(~small)[0]
        result[idx] = (torch.exp(z[idx]) - 1.0 - z[idx]) / (z[idx] ** 2)
    if bool(small.any()):
        idx = torch.where(small)[0]
        zi = z[idx]
        result[idx] = (
            0.5
            + zi / 6.0
            + zi**2 / 24.0
            + zi**3 / 120.0
            + zi**4 / 720.0
            + zi**5 / 5040.0
        )
    return result


def _triangle_transform_with_basis(
    *,
    origin: torch.Tensor,
    edge1: torch.Tensor,
    edge2: torch.Tensor,
    double_area_abs: torch.Tensor,
    omega: torch.Tensor,
    dtype: torch.dtype,
    eps: float,
) -> torch.Tensor:
    """Evaluate the simplex integral for one chosen triangle basis."""

    a_phase = -2j * math.pi * (omega @ edge1).to(dtype=dtype)
    b_phase = -2j * math.pi * (omega @ edge2).to(dtype=dtype)
    origin_phase = -2j * math.pi * (omega @ origin).to(dtype=dtype)
    zero_frequency = torch.linalg.norm(omega, dim=1) <= eps
    result = torch.empty(omega.shape[0], dtype=_complex_dtype(dtype), device=omega.device)
    regular = (~zero_frequency) & (torch.abs(b_phase) > eps)
    b_zero = (~zero_frequency) & (torch.abs(b_phase) <= eps)
    if bool(regular.any()):
        idx = torch.where(regular)[0]
        value = (
            torch.exp(b_phase[idx]) * _phi(a_phase[idx] - b_phase[idx], eps)
            - _phi(a_phase[idx], eps)
        ) / b_phase[idx]
        result[idx] = (double_area_abs * torch.exp(origin_phase[idx]) * value).to(
            dtype=_complex_dtype(dtype)
        )
    if bool(b_zero.any()):
        idx = torch.where(b_zero)[0]
        value = _psi(a_phase[idx], eps)
        result[idx] = (double_area_abs * torch.exp(origin_phase[idx]) * value).to(
            dtype=_complex_dtype(dtype)
        )
    if bool(zero_frequency.any()):
        zero_idx = torch.where(zero_frequency)[0]
        result[zero_idx] = (double_area_abs * 0.5).to(dtype=_complex_dtype(dtype))
    return result


def triangle_fourier_transform(
    triangle: torch.Tensor,
    omega: torch.Tensor,
    *,
    denominator_epsilon: float | None = None,
) -> torch.Tensor:
    """Return area integral for one triangle over all frequencies.

    The branch uses the closed-form simplex integral. Near-zero denominators use
    analytic series branches and therefore avoid direct division by near-zero
    denominators.
    """

    if triangle.shape != (3, 2):
        raise ValueError("triangle must have shape [3,2]")
    if omega.ndim != 2 or omega.shape[1] != 2:
        raise ValueError("omega must have shape [K,2]")
    dtype = torch.promote_types(triangle.dtype, omega.dtype)
    if dtype not in (torch.float32, torch.float64):
        dtype = torch.float32
    eps = denominator_epsilon
    if eps is None:
        eps = 1.0e-12 if dtype == torch.float64 else 1.0e-6
    triangle = triangle.to(device=omega.device, dtype=dtype)
    omega = omega.to(dtype=dtype)
    if not bool(torch.isfinite(triangle).all() and torch.isfinite(omega).all()):
        raise ValueError("triangle and omega must be finite")

    double_area = triangle_signed_double_area(triangle)
    double_area_abs = torch.abs(double_area)
    area = double_area_abs * 0.5
    if float(area.item()) == 0.0:
        return _zero_complex_like(omega, dtype)

    # The closed form is invariant to cyclic triangle parametrization, but the
    # direct branch is ill-conditioned when its denominator is close to zero.
    # Pick the cyclic basis with the largest |b_phase| per frequency so CPU and
    # CUDA take the same stable analytic path without changing the integral.
    bases = (
        (triangle[0], triangle[1] - triangle[0], triangle[2] - triangle[0]),
        (triangle[1], triangle[2] - triangle[1], triangle[0] - triangle[1]),
        (triangle[2], triangle[0] - triangle[2], triangle[1] - triangle[2]),
    )
    b_abs = torch.stack(
        [
            torch.abs((-2j * math.pi * (omega @ edge2).to(dtype=dtype)))
            for _origin, _edge1, edge2 in bases
        ],
        dim=0,
    )
    selected_basis = torch.argmax(b_abs, dim=0)
    result = torch.empty(omega.shape[0], dtype=_complex_dtype(dtype), device=omega.device)
    for basis_index, (origin, edge1, edge2) in enumerate(bases):
        idx = torch.where(selected_basis == basis_index)[0]
        if not bool(idx.numel()):
            continue
        partial = _triangle_transform_with_basis(
            origin=origin,
            edge1=edge1,
            edge2=edge2,
            double_area_abs=double_area_abs,
            omega=omega[idx],
            dtype=dtype,
            eps=eps,
        )
        result[idx] = partial
    if not bool(torch.isfinite(result.real).all() and torch.isfinite(result.imag).all()):
        raise ValueError("triangle Fourier output is nonfinite")
    return result
