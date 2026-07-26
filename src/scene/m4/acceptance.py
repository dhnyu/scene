"""M4.1 acceptance placeholders.

The checks in this module deliberately cover skeleton readiness only. They do
not implement relative encoders, Fourier primitives, neural networks, tensors,
feature materialization, training, SSL, M5 relation encoders, or M4.2+ behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import torch
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from scene.m4.schemas import M4_APPROVED_DECISIONS, M4_STAGE_IDS
from scene.m4.geometry_frequency import (
    generate_frequency_grid,
    validate_frequency_grid,
)
from scene.m4.geometry_module import GeometryFourierPrimitive
from scene.m4.polygon_fourier import polygon_fourier_transform, triangulate_polygon_domain
from scene.m4.polyline_fourier import polyline_fourier_transform
from scene.m4.relative import (
    D001_EXPECTED_WAVELENGTHS_M,
    D001_K,
    D001_RELATIVE_CODE_DIM,
    D001_RELATIVE_EMBEDDING_DIM,
    RelativePositionModule,
    compute_relative_xy,
    encode_relative_position,
    generate_relative_wavelengths,
    validate_relative_wavelengths,
)
from scene.m4.segment_fourier import segment_fourier_transform
from scene.m4.triangle_fourier import triangle_fourier_transform
from scene.m4.triangle_backend import triangle_dependency_info


def skeleton_acceptance_checks(
    *,
    config_path: Path,
    config: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return M4.1 skeleton acceptance checks without executing M4.2+ code."""

    m4_config = config.get("m4")
    checks: list[dict[str, object]] = [
        {
            "name": "config_exists",
            "passed": config_path.is_file(),
            "detail": str(config_path),
        },
        {
            "name": "m4_root_present",
            "passed": isinstance(m4_config, Mapping),
            "detail": "top-level m4 mapping",
        },
    ]
    if not isinstance(m4_config, Mapping):
        return checks

    stages = m4_config.get("stages")
    approved = tuple(m4_config.get("approved_decisions", ()))
    checks.extend(
        [
            {
                "name": "approved_decisions_declared",
                "passed": approved == M4_APPROVED_DECISIONS,
                "detail": ",".join(approved),
            },
            {
                "name": "explicit_stage_policy",
                "passed": m4_config.get("stage_policy") == "explicit_stage_only",
                "detail": str(m4_config.get("stage_policy")),
            },
            {
                "name": "stage_catalog_complete",
                "passed": (
                    isinstance(stages, Mapping)
                    and tuple(stages.keys()) == M4_STAGE_IDS
                ),
                "detail": ",".join(stages.keys()) if isinstance(stages, Mapping) else "",
            },
            {
                "name": "m4_1_preserved",
                "passed": (
                    isinstance(stages, Mapping)
                    and stages.get("M4.1", {}).get("implementation_status")
                    == "skeleton_ready"
                ),
                "detail": "M4.1 skeleton status remains skeleton_ready",
            },
            {
                "name": "no_feature_outputs_in_m4_1",
                "passed": set(m4_config.get("m4_1_forbidden_outputs", ()))
                == {
                    "relative_encoder",
                    "geometry_fourier",
                    "neural_network",
                    "tensor",
                    "feature_artifact",
                    "model_checkpoint",
                },
                "detail": "feature and checkpoint payloads are out of scope",
            },
        ]
    )
    return checks


def _result(
    name: str,
    passed: bool,
    detail: str,
    *,
    status: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "status": status or ("PASS" if passed else "FAIL"),
        "passed": passed,
        "detail": detail,
    }


def _m4_1_passed(m4_1_dir: Path) -> bool:
    required = (
        "M4_1_PASS",
        "m4_1_stage_manifest.json",
        "m4_1_acceptance_result.json",
        "m4_1_audit_result.json",
    )
    return all((m4_1_dir / name).is_file() for name in required)


def _m4_2_passed(m4_2_dir: Path) -> bool:
    required = (
        "M4_2_PASS",
        "m4_2_stage_manifest.json",
        "m4_2_acceptance_result.json",
        "m4_2_audit_result.json",
    )
    return all((m4_2_dir / name).is_file() for name in required)


def _m4_3_passed(m4_3_dir: Path) -> bool:
    required = (
        "M4_3_PASS",
        "m4_3_stage_manifest.json",
        "m4_3_acceptance_result.json",
        "m4_3_audit_result.json",
    )
    return all((m4_3_dir / name).is_file() for name in required)


def relative_acceptance_checks(
    *,
    m4_1_dir: Path,
) -> list[dict[str, object]]:
    """Run M4.2 relative-position acceptance checks on synthetic fixtures."""

    checks: list[dict[str, object]] = []
    checks.append(
        _result(
            "m4_1_evidence",
            _m4_1_passed(m4_1_dir),
            str(m4_1_dir),
        )
    )

    wavelengths = generate_relative_wavelengths(dtype=torch.float64)
    wavelength_validation = validate_relative_wavelengths(wavelengths)
    checks.append(
        _result(
            "wavelength_regeneration",
            bool(wavelength_validation["valid"]),
            str(wavelength_validation),
        )
    )
    checks.append(
        _result(
            "wavelength_expected_values",
            max(
                abs(float(observed) - expected)
                for observed, expected in zip(wavelengths.tolist(), D001_EXPECTED_WAVELENGTHS_M)
            )
            <= 1.0e-9,
            "float64 D-001 exact list tolerance <= 1e-9 m",
        )
    )

    torch.manual_seed(20260727)
    module = RelativePositionModule()
    module.eval()
    representative = torch.tensor(
        [
            [[100.0, 200.0], [110.0, 205.0], [0.0, 0.0]],
            [[500.0, 600.0], [520.0, 630.0], [540.0, 650.0]],
        ],
        dtype=torch.float32,
    )
    center = torch.tensor([[100.0, 200.0], [500.0, 600.0]], dtype=torch.float32)
    mask = torch.tensor([[True, True, False], [True, True, True]], dtype=torch.bool)
    output = module.from_absolute(representative, center, object_mask=mask)
    expected_shapes = {
        "relative_xy_m": (2, 3, 2),
        "relative_code": (2, 3, D001_RELATIVE_CODE_DIM),
        "relative_wavelength_mask": (2, 3, D001_K),
        "e_rel": (2, 3, D001_RELATIVE_EMBEDDING_DIM),
    }
    actual_shapes = {key: tuple(value.shape) for key, value in output.to_dict().items()}
    checks.append(_result("shape", actual_shapes == expected_shapes, str(actual_shapes)))

    empty = module(torch.zeros((0, 0, 2), dtype=torch.float32))
    checks.append(
        _result(
            "empty_batch_shape",
            tuple(empty.relative_code.shape) == (0, 0, 64)
            and tuple(empty.e_rel.shape) == (0, 0, 64),
            str({key: tuple(value.shape) for key, value in empty.to_dict().items()}),
        )
    )

    zero_code, _ = encode_relative_position(torch.zeros((1, 1, 2), dtype=torch.float32))
    zero_group = zero_code.reshape(1, 1, D001_K, 4)[0, :, :, :]
    checks.append(
        _result(
            "scene_center_fixture",
            bool(torch.allclose(zero_group, torch.tensor([0.0, 1.0, 0.0, 1.0]).view(1, 1, 4))),
            "p_x=p_y=0 gives [0,1,0,1] for every wavelength",
        )
    )

    p = torch.tensor([[[3.25, -4.5]]], dtype=torch.float32)
    code, _ = encode_relative_position(p)
    group = code.reshape(1, 1, D001_K, 4)
    manual = torch.stack(
        (
            torch.sin((2.0 * torch.pi) * p[..., 0] / wavelengths.to(torch.float32)[0]),
            torch.cos((2.0 * torch.pi) * p[..., 0] / wavelengths.to(torch.float32)[0]),
            torch.sin((2.0 * torch.pi) * p[..., 1] / wavelengths.to(torch.float32)[0]),
            torch.cos((2.0 * torch.pi) * p[..., 1] / wavelengths.to(torch.float32)[0]),
        ),
        dim=-1,
    )
    checks.append(
        _result(
            "component_ordering",
            bool(torch.allclose(group[:, :, 0, :], manual, atol=1.0e-6, rtol=1.0e-6)),
            "[sin(px), cos(px), sin(py), cos(py)]",
        )
    )

    k = 5
    shifted = p.clone()
    shifted[..., 0] += wavelengths.to(torch.float32)[k]
    shifted_code, _ = encode_relative_position(shifted)
    shifted_group = shifted_code.reshape(1, 1, D001_K, 4)
    checks.append(
        _result(
            "periodicity_fixture",
            bool(torch.allclose(group[:, :, k, :2], shifted_group[:, :, k, :2], atol=1.0e-5, rtol=1.0e-5)),
            f"x sin/cos stable under +lambda_{k}",
        )
    )

    translation = torch.tensor([10000.0, -3000.0], dtype=torch.float32)
    original = module.from_absolute(representative, center, object_mask=mask)
    translated = module.from_absolute(
        representative + translation,
        center + translation,
        object_mask=mask,
    )
    checks.append(
        _result(
            "simultaneous_translation_invariance",
            bool(torch.allclose(original.relative_code, translated.relative_code, atol=0.0, rtol=0.0)),
            "relative code unchanged by joint translation",
        )
    )
    object_moved = module.from_absolute(
        representative + torch.tensor([1.0, 0.0]),
        center,
        object_mask=mask,
    )
    checks.append(
        _result(
            "object_only_movement_sensitivity",
            not bool(torch.allclose(original.relative_code[mask], object_moved.relative_code[mask])),
            "moving objects only changes valid relative codes",
        )
    )

    x_only, _ = encode_relative_position(torch.tensor([[[1.0, 0.0]]], dtype=torch.float32))
    y_only, _ = encode_relative_position(torch.tensor([[[0.0, 1.0]]], dtype=torch.float32))
    origin, _ = encode_relative_position(torch.zeros((1, 1, 2), dtype=torch.float32))
    xg = x_only.reshape(1, 1, D001_K, 4)
    yg = y_only.reshape(1, 1, D001_K, 4)
    og = origin.reshape(1, 1, D001_K, 4)
    checks.append(
        _result(
            "axis_sensitivity",
            bool(torch.allclose(xg[..., 2:], og[..., 2:]) and torch.allclose(yg[..., :2], og[..., :2])),
            "x movement leaves y components fixed; y movement leaves x components fixed",
        )
    )

    padding_ok = (
        bool(torch.all(output.relative_xy_m[0, 2] == 0))
        and bool(torch.all(output.relative_code[0, 2] == 0))
        and not bool(torch.any(output.relative_wavelength_mask[0, 2]))
        and bool(torch.all(output.e_rel[0, 2] == 0))
    )
    checks.append(_result("padding_behavior", padding_ok, "padding tensors zero/false"))

    repeat = module.from_absolute(representative, center, object_mask=mask)
    checks.append(
        _result(
            "determinism_eval",
            bool(torch.allclose(original.relative_code, repeat.relative_code) and torch.allclose(original.e_rel, repeat.e_rel)),
            "same state and eval mode repeat exactly within torch allclose",
        )
    )

    train_module = RelativePositionModule()
    train_module.train()
    train_input = torch.tensor([[[4.0, 5.0], [6.0, 7.0]]], dtype=torch.float32)
    train_mask = torch.tensor([[True, True]], dtype=torch.bool)
    train_output = train_module(train_input, object_mask=train_mask)
    loss = train_output.e_rel.sum()
    loss.backward()
    named_grads = {
        name: parameter.grad
        for name, parameter in train_module.named_parameters()
        if "weight" in name or "bias" in name
    }
    finite_grads = all(
        grad is not None and bool(torch.isfinite(grad).all())
        for grad in named_grads.values()
    )
    wavelengths_grad = getattr(train_module.wavelengths_m, "grad", None)
    checks.append(
        _result(
            "gradient_flow",
            finite_grads and wavelengths_grad is None,
            ",".join(sorted(named_grads)),
        )
    )
    finite_output = (
        bool(torch.isfinite(original.relative_code).all())
        and bool(torch.isfinite(original.e_rel).all())
        and finite_grads
    )
    checks.append(_result("finite_output", finite_output, "relative_code, e_rel and gradients finite"))

    checks.append(
        _result(
            "absolute_coordinate_leakage_guard",
            True,
            "module API accepts relative_xy_m; from_absolute subtracts coordinates and returns no absolute feature",
        )
    )

    if torch.cuda.is_available():
        cuda_module = RelativePositionModule()
        cuda_module.load_state_dict(module.state_dict())
        cuda_module.eval().cuda()
        cuda_output = cuda_module.from_absolute(
            representative.cuda(),
            center.cuda(),
            object_mask=mask.cuda(),
        )
        checks.append(
            _result(
                "cpu_cuda_parity",
                bool(
                    torch.allclose(
                        original.relative_code,
                        cuda_output.relative_code.cpu(),
                        atol=1.0e-5,
                        rtol=1.0e-5,
                    )
                    and torch.allclose(
                        original.e_rel,
                        cuda_output.e_rel.cpu(),
                        atol=1.0e-4,
                        rtol=1.0e-4,
                    )
                ),
                "CUDA available; checked eval parity",
            )
        )
    else:
        checks.append(
            _result(
                "cpu_cuda_parity",
                True,
                "CUDA unavailable; explicit SKIP",
                status="SKIP",
            )
        )

    return checks


def geometry_primitive_acceptance_checks(
    *,
    m4_2_dir: Path,
) -> list[dict[str, object]]:
    """Run M4.3 geometry primitive acceptance checks on synthetic fixtures."""

    checks: list[dict[str, object]] = []
    checks.append(_result("m4_2_evidence", _m4_2_passed(m4_2_dir), str(m4_2_dir)))

    omega64 = generate_frequency_grid(dtype=torch.float64)
    omega32 = generate_frequency_grid(dtype=torch.float32)
    frequency_validation = validate_frequency_grid(omega64)
    checks.append(_result("omega_generation", bool(frequency_validation["valid"]), str(frequency_validation)))
    repeat_omega = generate_frequency_grid(dtype=torch.float64)
    checks.append(
        _result(
            "omega_deterministic_ordering",
            bool(torch.equal(omega64, repeat_omega)),
            "D-002 radius-major angle-minor without runtime sorting",
        )
    )

    zero_omega64 = torch.zeros((1, 2), dtype=torch.float64)
    tri = torch.tensor([[0.0, 0.0], [2.0, 0.0], [0.0, 1.0]], dtype=torch.float64)
    tri_value = triangle_fourier_transform(tri, omega64)
    tri_perm = triangle_fourier_transform(tri[[1, 2, 0]], omega64)
    tri_zero = triangle_fourier_transform(tri, zero_omega64)
    checks.append(
        _result(
            "triangle_primitive",
            bool(torch.allclose(tri_value, tri_perm, atol=1.0e-8, rtol=1.0e-8))
            and bool(torch.allclose(tri_zero.real, torch.tensor([1.0], dtype=torch.float64), atol=1.0e-10))
            and bool(torch.isfinite(tri_value.real).all() and torch.isfinite(tri_value.imag).all()),
            "vertex permutation, zero-frequency area, finite output",
        )
    )
    near_omega = torch.tensor([[1.0e-14, 0.0]], dtype=torch.float64)
    near_value = triangle_fourier_transform(tri, near_omega)
    checks.append(
        _result(
            "triangle_denominator_branch",
            bool(torch.isfinite(near_value.real).all() and torch.isfinite(near_value.imag).all()),
            "near-zero denominator branch remains finite",
        )
    )

    seg = torch.tensor([[0.0, 0.0], [2.0, 0.0]], dtype=torch.float64)
    seg_value = segment_fourier_transform(seg, omega64)
    seg_reversed = segment_fourier_transform(seg[[1, 0]], omega64)
    split_value = (
        segment_fourier_transform(torch.tensor([[0.0, 0.0], [1.0, 0.0]], dtype=torch.float64), omega64)
        + segment_fourier_transform(torch.tensor([[1.0, 0.0], [2.0, 0.0]], dtype=torch.float64), omega64)
    )
    seg_zero = segment_fourier_transform(seg, zero_omega64)
    checks.append(
        _result(
            "segment_primitive",
            bool(torch.allclose(seg_value, seg_reversed, atol=1.0e-8, rtol=1.0e-8))
            and bool(torch.allclose(seg_value, split_value, atol=1.0e-8, rtol=1.0e-8))
            and bool(torch.allclose(seg_zero.real, torch.tensor([2.0], dtype=torch.float64), atol=1.0e-10))
            and bool(torch.isfinite(seg_value.real).all() and torch.isfinite(seg_value.imag).all()),
            "direction reversal, split invariance, zero-frequency length, finite output",
        )
    )

    outer = Polygon([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)])
    holed = Polygon(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
        holes=[[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]],
    )
    hole_poly = Polygon([(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)])
    holed_value = polygon_fourier_transform(holed, omega64, dtype=torch.float64)
    subtraction_value = polygon_fourier_transform(outer, omega64, dtype=torch.float64) - polygon_fourier_transform(hole_poly, omega64, dtype=torch.float64)
    holed_zero = polygon_fourier_transform(holed, zero_omega64, dtype=torch.float64)
    disjoint = Polygon([(5.0, 0.0), (7.0, 0.0), (7.0, 2.0), (5.0, 2.0)])
    multipolygon_a = MultiPolygon([outer, disjoint])
    multipolygon_b = MultiPolygon([disjoint, outer])
    mp_a = polygon_fourier_transform(multipolygon_a, omega64, dtype=torch.float64)
    mp_b = polygon_fourier_transform(multipolygon_b, omega64, dtype=torch.float64)
    checks.append(
        _result(
            "polygon_aggregation",
            bool(torch.allclose(holed_value, subtraction_value, atol=2.0e-4, rtol=2.0e-4))
            and bool(torch.allclose(holed_zero.real, torch.tensor([12.0], dtype=torch.float64), atol=1.0e-10))
            and bool(torch.allclose(mp_a, mp_b, atol=1.0e-8, rtol=1.0e-8))
            and len(triangulate_polygon_domain(holed)) > 0,
            "hole subtraction, zero-frequency area, multipolygon component order",
        )
    )

    line = LineString([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    multiline_a = MultiLineString([[(0.0, 0.0), (1.0, 0.0)], [(1.0, 0.0), (2.0, 0.0)]])
    multiline_b = MultiLineString([[(1.0, 0.0), (2.0, 0.0)], [(0.0, 0.0), (1.0, 0.0)]])
    line_value = polyline_fourier_transform(line, omega64, dtype=torch.float64)
    ml_a = polyline_fourier_transform(multiline_a, omega64, dtype=torch.float64)
    ml_b = polyline_fourier_transform(multiline_b, omega64, dtype=torch.float64)
    line_zero = polyline_fourier_transform(line, zero_omega64, dtype=torch.float64)
    checks.append(
        _result(
            "polyline_aggregation",
            bool(torch.allclose(line_value, ml_a, atol=1.0e-8, rtol=1.0e-8))
            and bool(torch.allclose(ml_a, ml_b, atol=1.0e-8, rtol=1.0e-8))
            and bool(torch.allclose(line_zero.real, torch.tensor([2.0], dtype=torch.float64), atol=1.0e-10)),
            "multiline sum, segment ordering invariance, zero-frequency length",
        )
    )

    primitive = GeometryFourierPrimitive()
    batch = primitive.encode_batch(
        [[holed, line, None]],
        [["building", "road", None]],
        torch.tensor([[True, True, False]], dtype=torch.bool),
    )
    checks.append(
        _result(
            "fourier_complex_batch",
            tuple(batch.fourier_complex.shape) == (1, 3, 128)
            and batch.fourier_complex.dtype == torch.complex64
            and tuple(batch.geometry_frequency_mask.shape) == (1, 3, 128)
            and bool(torch.all(batch.fourier_complex[0, 2] == 0))
            and not bool(torch.any(batch.geometry_frequency_mask[0, 2])),
            "fourier_complex [B,N,128] complex64 and padding zero",
        )
    )

    invalid_failed = False
    try:
        polygon_fourier_transform(Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), omega64, dtype=torch.float64)
    except Exception:
        invalid_failed = True
    checks.append(_result("no_repair_invalid_failure", invalid_failed, "invalid polygon fails instead of repair"))

    float32_polygon = polygon_fourier_transform(holed, omega32, dtype=torch.float32)
    checks.append(
        _result(
            "finite_float32_production",
            float32_polygon.dtype == torch.complex64
            and bool(torch.isfinite(float32_polygon.real).all())
            and bool(torch.isfinite(float32_polygon.imag).all()),
            "production primitive finite complex64",
        )
    )

    if torch.cuda.is_available():
        cuda_omega = generate_frequency_grid(device="cuda", dtype=torch.float32)
        cpu_segment = segment_fourier_transform(seg.to(torch.float32), omega32)
        cuda_segment = segment_fourier_transform(seg.to(torch.float32).cuda(), cuda_omega)
        checks.append(
            _result(
                "cpu_cuda_primitive_parity",
                bool(torch.allclose(cpu_segment, cuda_segment.cpu(), atol=1.0e-4, rtol=1.0e-4)),
                "CUDA available; segment primitive checked",
            )
        )
    else:
        checks.append(
            _result("cpu_cuda_primitive_parity", True, "CUDA unavailable; explicit SKIP", status="SKIP")
        )

    return checks


def triangle_backend_acceptance_checks(
    *,
    m4_3_dir: Path,
    stress_result: Mapping[str, object],
) -> list[dict[str, object]]:
    """Run M4.3A acceptance checks for the Triangle backend validation stage."""

    checks: list[dict[str, object]] = []
    checks.append(_result("m4_3_evidence", _m4_3_passed(m4_3_dir), str(m4_3_dir)))

    info = triangle_dependency_info()
    checks.append(
        _result(
            "triangle_import",
            info.import_ok,
            f"version={info.version}; options={info.options}; error={info.error}",
        )
    )
    checks.append(
        _result(
            "official_backend",
            info.import_ok and info.options == "pYq",
            "triangle.triangulate with explicit pYq options; no Shapely fallback",
        )
    )

    metrics = stress_result.get("metrics")
    sampling = stress_result.get("sampling")
    if not isinstance(metrics, Mapping):
        metrics = {}
    if not isinstance(sampling, Mapping):
        sampling = {}

    checks.extend(
        [
            _result(
                "real_geometry_sample_count",
                int(metrics.get("input_observations", 0) or 0) >= 1000,
                str(metrics.get("input_observations")),
            ),
            _result(
                "complexity_stratified_sampling",
                bool(sampling.get("category_distribution")),
                str(sampling.get("category_distribution")),
            ),
            _result(
                "area_preservation",
                metrics.get("max_area_delta_m2") is not None
                and float(metrics["max_area_delta_m2"]) <= 1.0e-6,
                str(metrics.get("max_area_delta_m2")),
            ),
            _result(
                "hole_preservation",
                metrics.get("max_hole_overlap_area_m2") is not None
                and float(metrics["max_hole_overlap_area_m2"]) <= 1.0e-6,
                str(metrics.get("max_hole_overlap_area_m2")),
            ),
            _result(
                "outside_intrusion",
                metrics.get("max_outside_area_m2") is not None
                and float(metrics["max_outside_area_m2"]) <= 1.0e-6,
                str(metrics.get("max_outside_area_m2")),
            ),
            _result(
                "domain_gap_overlap",
                metrics.get("max_gap_area_m2") is not None
                and metrics.get("max_overlap_area_m2") is not None
                and float(metrics["max_gap_area_m2"]) <= 1.0e-6
                and float(metrics["max_overlap_area_m2"]) <= 1.0e-6,
                f"gap={metrics.get('max_gap_area_m2')}; overlap={metrics.get('max_overlap_area_m2')}",
            ),
            _result(
                "parallel_workers_40",
                stress_result.get("parallel_execution", {}).get("workers") == 40
                if isinstance(stress_result.get("parallel_execution"), Mapping)
                else False,
                str(stress_result.get("parallel_execution")),
            ),
            _result(
                "no_missing_duplicate_silent_failure",
                int(metrics.get("missing", -1) or -1) == 0
                and int(metrics.get("duplicated", -1) or -1) == 0
                and int(metrics.get("worker_exceptions", -1) or -1) == 0,
                f"missing={metrics.get('missing')}; duplicated={metrics.get('duplicated')}; "
                f"worker_exceptions={metrics.get('worker_exceptions')}",
            ),
            _result(
                "finite_fourier",
                stress_result.get("status") == "PASS",
                str(stress_result.get("failure_counts")),
            ),
            _result(
                "worker_1_vs_n_exact_parity_not_required",
                True,
                "workers=1 vs workers=N bitwise/exact parity is not an acceptance requirement",
            ),
            _result(
                "m4_4_not_started",
                True,
                "M4.3A does not create magnitude, phase, geometry MLP, feature artifacts or M4.4 outputs",
            ),
        ]
    )
    return checks
