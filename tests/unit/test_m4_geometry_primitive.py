from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from scene.m4.acceptance import geometry_primitive_acceptance_checks
from scene.m4.geometry_encoder import fourier_to_magnitude_phase, initialize_geometry_encoder
from scene.m4.geometry_frequency import generate_frequency_grid, validate_frequency_grid
from scene.m4.geometry_module import GeometryFourierPrimitive
from scene.m4.polygon_fourier import GeometryPrimitiveError, polygon_fourier_transform
from scene.m4.polyline_fourier import polyline_fourier_transform
from scene.m4.segment_fourier import segment_fourier_transform
from scene.m4.triangle_fourier import triangle_fourier_transform
from scene.m4.workflow import run_m4_stage

pytest.importorskip("triangle")


def test_d002_frequency_grid_contract() -> None:
    omega = generate_frequency_grid(dtype=torch.float64)
    validation = validate_frequency_grid(omega)

    assert validation["valid"] is True
    assert omega.shape == (128, 2)
    assert torch.isfinite(omega).all()
    assert not torch.any(torch.linalg.norm(omega, dim=1) == 0)
    assert torch.equal(omega, generate_frequency_grid(dtype=torch.float64))
    assert omega[0, 0].item() == pytest.approx(0.5)
    assert omega[0, 1].item() == pytest.approx(0.0)


def test_triangle_primitive_permutation_zero_area_and_finite() -> None:
    omega = generate_frequency_grid(dtype=torch.float64)
    tri = torch.tensor([[0.0, 0.0], [2.0, 0.0], [0.0, 1.0]], dtype=torch.float64)

    value = triangle_fourier_transform(tri, omega)
    permuted = triangle_fourier_transform(tri[[1, 2, 0]], omega)
    zero = triangle_fourier_transform(tri, torch.zeros((1, 2), dtype=torch.float64))
    near = triangle_fourier_transform(tri, torch.tensor([[1.0e-14, 0.0]], dtype=torch.float64))

    assert value.shape == (128,)
    assert value.dtype == torch.complex128
    assert torch.allclose(value, permuted, atol=1.0e-8, rtol=1.0e-8)
    assert zero.real.item() == pytest.approx(1.0, abs=1.0e-10)
    assert torch.isfinite(value.real).all()
    assert torch.isfinite(value.imag).all()
    assert torch.isfinite(near.real).all()
    assert torch.isfinite(near.imag).all()


def test_segment_primitive_split_reversal_and_zero_frequency_length() -> None:
    omega = generate_frequency_grid(dtype=torch.float64)
    segment = torch.tensor([[0.0, 0.0], [2.0, 0.0]], dtype=torch.float64)

    direct = segment_fourier_transform(segment, omega)
    reversed_value = segment_fourier_transform(segment[[1, 0]], omega)
    split = (
        segment_fourier_transform(torch.tensor([[0.0, 0.0], [1.0, 0.0]], dtype=torch.float64), omega)
        + segment_fourier_transform(torch.tensor([[1.0, 0.0], [2.0, 0.0]], dtype=torch.float64), omega)
    )
    zero = segment_fourier_transform(segment, torch.zeros((1, 2), dtype=torch.float64))

    assert torch.allclose(direct, reversed_value, atol=1.0e-8, rtol=1.0e-8)
    assert torch.allclose(direct, split, atol=1.0e-8, rtol=1.0e-8)
    assert zero.real.item() == pytest.approx(2.0, abs=1.0e-10)
    assert torch.isfinite(direct.real).all()
    assert torch.isfinite(direct.imag).all()


def test_polygon_hole_multipolygon_and_no_repair_policy() -> None:
    omega = generate_frequency_grid(dtype=torch.float64)
    outer = Polygon([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)])
    hole = Polygon([(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)])
    holed = Polygon(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
        holes=[[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]],
    )

    holed_value = polygon_fourier_transform(holed, omega, dtype=torch.float64)
    expected = polygon_fourier_transform(outer, omega, dtype=torch.float64) - polygon_fourier_transform(hole, omega, dtype=torch.float64)
    zero = polygon_fourier_transform(holed, torch.zeros((1, 2), dtype=torch.float64), dtype=torch.float64)
    disjoint = Polygon([(5.0, 0.0), (7.0, 0.0), (7.0, 2.0), (5.0, 2.0)])
    mp_a = polygon_fourier_transform(MultiPolygon([outer, disjoint]), omega, dtype=torch.float64)
    mp_b = polygon_fourier_transform(MultiPolygon([disjoint, outer]), omega, dtype=torch.float64)

    assert torch.allclose(holed_value, expected, atol=2.0e-4, rtol=2.0e-4)
    assert zero.real.item() == pytest.approx(12.0, abs=1.0e-10)
    assert torch.allclose(mp_a, mp_b, atol=1.0e-8, rtol=1.0e-8)
    with pytest.raises(GeometryPrimitiveError):
        polygon_fourier_transform(Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), omega, dtype=torch.float64)


def test_polyline_multiline_order_and_batch_output() -> None:
    omega = generate_frequency_grid(dtype=torch.float64)
    line = LineString([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    ml_a = MultiLineString([[(0.0, 0.0), (1.0, 0.0)], [(1.0, 0.0), (2.0, 0.0)]])
    ml_b = MultiLineString([[(1.0, 0.0), (2.0, 0.0)], [(0.0, 0.0), (1.0, 0.0)]])

    assert torch.allclose(
        polyline_fourier_transform(line, omega, dtype=torch.float64),
        polyline_fourier_transform(ml_a, omega, dtype=torch.float64),
        atol=1.0e-8,
        rtol=1.0e-8,
    )
    assert torch.allclose(
        polyline_fourier_transform(ml_a, omega, dtype=torch.float64),
        polyline_fourier_transform(ml_b, omega, dtype=torch.float64),
        atol=1.0e-8,
        rtol=1.0e-8,
    )

    primitive = GeometryFourierPrimitive()
    output = primitive.encode_batch(
        [[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]), line, None]],
        [["building", "road", None]],
        torch.tensor([[True, True, False]], dtype=torch.bool),
    )
    assert output.fourier_complex.shape == (1, 3, 128)
    assert output.fourier_complex.dtype == torch.complex64
    assert output.geometry_frequency_mask.shape == (1, 3, 128)
    assert torch.all(output.fourier_complex[0, 2] == 0)
    assert not torch.any(output.geometry_frequency_mask[0, 2])


def test_geometry_primitive_acceptance_and_stage_runner(tmp_path: Path) -> None:
    m4_2 = tmp_path / "M4.2"
    m4_2.mkdir()
    for filename in (
        "M4_2_PASS",
        "m4_2_stage_manifest.json",
        "m4_2_acceptance_result.json",
        "m4_2_audit_result.json",
    ):
        (m4_2 / filename).write_text("{}\n", encoding="utf-8")

    checks = geometry_primitive_acceptance_checks(m4_2_dir=m4_2)
    assert not [check for check in checks if check["status"] == "FAIL"]

    result = run_m4_stage(
        Path("configs/m4/m4_skeleton.yaml"),
        stage_id="M4.3",
        output_dir=tmp_path / "out",
        m4_2_dir=m4_2,
    )
    assert result["status"] == "PASS"
    stage_dir = Path(str(result["stage_dir"]))
    manifest = json.loads((stage_dir / "m4_3_stage_manifest.json").read_text())
    assert manifest["next_stage"] == "M4.4"
    assert manifest["auto_continue"] is False
    assert (stage_dir / "M4_3_PASS").is_file()
    assert not any(stage_dir.glob("*.pt"))
    assert not any(stage_dir.glob("*.parquet"))


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_segment_cpu_cuda_parity() -> None:
    segment = torch.tensor([[0.0, 0.0], [2.0, 0.0]], dtype=torch.float32)
    cpu = segment_fourier_transform(segment, generate_frequency_grid(dtype=torch.float32))
    cuda = segment_fourier_transform(
        segment.cuda(),
        generate_frequency_grid(device="cuda", dtype=torch.float32),
    )
    assert torch.allclose(cpu, cuda.cpu(), atol=1.0e-4, rtol=1.0e-4)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_polygon_cpu_cuda_parity_covers_hole_multipolygon_and_shared_coordinate() -> None:
    fixtures = [
        Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
        Polygon(
            [(0, 0), (4, 0), (4, 4), (0, 4)],
            holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]],
        ),
        MultiPolygon(
            [
                Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
                Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
            ]
        ),
        Polygon(
            [(0, 0), (5, 0), (5, 5), (0, 5)],
            holes=[[(0, 0), (1, 0.5), (0.5, 1)]],
        ),
    ]
    model = initialize_geometry_encoder()
    model.eval()
    cpu_omega = generate_frequency_grid(dtype=torch.float32)
    cuda_omega = generate_frequency_grid(device="cuda", dtype=torch.float32)
    for geometry in fixtures:
        cpu_fourier = polygon_fourier_transform(geometry, cpu_omega, dtype=torch.float32)
        cuda_fourier = polygon_fourier_transform(geometry, cuda_omega, dtype=torch.float32).cpu()
        cpu_features = fourier_to_magnitude_phase(cpu_fourier.unsqueeze(0))
        cuda_features = fourier_to_magnitude_phase(cuda_fourier.unsqueeze(0))
        with torch.no_grad():
            cpu_encoded = model(cpu_features.x_mag, cpu_features.x_phase)
            cuda_encoded = model(cuda_features.x_mag, cuda_features.x_phase)

        assert torch.allclose(cpu_fourier, cuda_fourier, atol=1.0e-4, rtol=1.0e-4)
        assert torch.allclose(
            cpu_features.fourier_magnitude,
            cuda_features.fourier_magnitude,
            atol=1.0e-4,
            rtol=1.0e-4,
        )
        assert torch.allclose(cpu_features.x_mag, cuda_features.x_mag, atol=1.0e-4, rtol=1.0e-4)
        assert torch.allclose(cpu_features.x_phase, cuda_features.x_phase, atol=1.0e-4, rtol=1.0e-4)
        assert torch.allclose(cpu_encoded.e_geom, cuda_encoded.e_geom, atol=1.0e-4, rtol=1.0e-4)
