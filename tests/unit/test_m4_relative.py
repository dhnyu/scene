from __future__ import annotations

from pathlib import Path

import pytest
import torch

from scene.m4.acceptance import relative_acceptance_checks
from scene.m4.relative import (
    D001_EXPECTED_WAVELENGTHS_M,
    D001_K,
    RelativePositionEncoder,
    RelativePositionModule,
    compute_relative_xy,
    encode_relative_position,
    generate_relative_wavelengths,
    validate_relative_wavelengths,
)
from scene.m4.workflow import run_m4_stage


def test_d001_wavelength_regeneration_contract() -> None:
    wavelengths = generate_relative_wavelengths(dtype=torch.float64)
    validation = validate_relative_wavelengths(wavelengths)

    assert validation["valid"] is True
    assert validation["k"] == 16
    assert wavelengths.dtype == torch.float64
    assert wavelengths[0].item() == pytest.approx(10.0, abs=1.0e-12)
    assert wavelengths[-1].item() == pytest.approx(1000.0, abs=1.0e-12)
    assert torch.all(wavelengths[1:] > wavelengths[:-1])
    assert torch.unique(wavelengths).numel() == D001_K
    assert torch.isfinite(wavelengths).all()
    assert max(
        abs(observed - expected)
        for observed, expected in zip(wavelengths.tolist(), D001_EXPECTED_WAVELENGTHS_M)
    ) <= 1.0e-9


def test_relative_code_shape_order_center_and_periodicity() -> None:
    wavelengths = generate_relative_wavelengths(dtype=torch.float64)
    relative_xy = torch.tensor([[[0.0, 0.0], [2.5, -4.0]]], dtype=torch.float32)
    code, wavelength_mask = encode_relative_position(relative_xy)

    assert code.shape == (1, 2, 64)
    assert code.dtype == torch.float32
    assert wavelength_mask.shape == (1, 2, 16)
    assert wavelength_mask.dtype == torch.bool
    groups = code.reshape(1, 2, 16, 4)
    assert torch.allclose(
        groups[0, 0],
        torch.tensor([0.0, 1.0, 0.0, 1.0]).expand(16, 4),
        atol=1.0e-6,
        rtol=0.0,
    )

    px = relative_xy[0, 1, 0]
    py = relative_xy[0, 1, 1]
    lam0 = wavelengths.to(torch.float32)[0]
    expected_group0 = torch.tensor(
        [
            torch.sin((2.0 * torch.pi) * px / lam0),
            torch.cos((2.0 * torch.pi) * px / lam0),
            torch.sin((2.0 * torch.pi) * py / lam0),
            torch.cos((2.0 * torch.pi) * py / lam0),
        ]
    )
    assert torch.allclose(groups[0, 1, 0], expected_group0, atol=1.0e-6, rtol=1.0e-6)

    shifted = relative_xy[:, 1:2].clone()
    shifted[..., 0] += wavelengths.to(torch.float32)[7]
    shifted_code, _ = encode_relative_position(shifted)
    shifted_groups = shifted_code.reshape(1, 1, 16, 4)
    assert torch.allclose(
        groups[:, 1:2, 7, :2],
        shifted_groups[:, :, 7, :2],
        atol=1.0e-5,
        rtol=1.0e-5,
    )


def test_translation_and_axis_behavior() -> None:
    module = RelativePositionModule()
    module.eval()
    representative = torch.tensor([[[100.0, 200.0], [120.0, 230.0]]])
    center = torch.tensor([[100.0, 200.0]])
    translated = torch.tensor([12345.0, -777.0])

    original = module.from_absolute(representative, center)
    moved_together = module.from_absolute(representative + translated, center + translated)
    moved_object = module.from_absolute(representative + torch.tensor([1.0, 0.0]), center)

    assert torch.allclose(original.relative_code, moved_together.relative_code)
    assert not torch.allclose(original.relative_code, moved_object.relative_code)

    origin, _ = encode_relative_position(torch.zeros((1, 1, 2)))
    x_only, _ = encode_relative_position(torch.tensor([[[1.0, 0.0]]]))
    y_only, _ = encode_relative_position(torch.tensor([[[0.0, 1.0]]]))
    og = origin.reshape(1, 1, 16, 4)
    xg = x_only.reshape(1, 1, 16, 4)
    yg = y_only.reshape(1, 1, 16, 4)
    assert torch.allclose(xg[..., 2:], og[..., 2:])
    assert torch.allclose(yg[..., :2], og[..., :2])


def test_padding_behavior_and_gradient_flow() -> None:
    module = RelativePositionModule()
    module.train()
    relative_xy = torch.tensor([[[1.0, 2.0], [0.0, 0.0]]], dtype=torch.float32)
    object_mask = torch.tensor([[True, False]])
    output = module(relative_xy, object_mask=object_mask)

    assert torch.all(output.relative_xy_m[0, 1] == 0)
    assert torch.all(output.relative_code[0, 1] == 0)
    assert not torch.any(output.relative_wavelength_mask[0, 1])
    assert torch.all(output.e_rel[0, 1] == 0)

    loss = output.e_rel[object_mask].sum()
    loss.backward()
    assert module.wavelengths_m.grad is None
    assert all(
        parameter.grad is not None and torch.isfinite(parameter.grad).all()
        for parameter in module.parameters()
    )


def test_relative_encoder_architecture_and_no_absolute_feature_api() -> None:
    encoder = RelativePositionEncoder()
    layers = list(encoder.network)

    assert isinstance(layers[0], torch.nn.Linear)
    assert layers[0].in_features == 64
    assert layers[0].out_features == 128
    assert isinstance(layers[1], torch.nn.LayerNorm)
    assert isinstance(layers[2], torch.nn.GELU)
    assert isinstance(layers[3], torch.nn.Dropout)
    assert layers[3].p == pytest.approx(0.1)
    assert isinstance(layers[4], torch.nn.Linear)
    assert layers[4].in_features == 128
    assert layers[4].out_features == 64

    representative = torch.tensor([[[10.0, 20.0]]])
    center = torch.tensor([[3.0, 4.0]])
    relative = compute_relative_xy(representative, center)
    assert relative.tolist() == [[[7.0, 16.0]]]


def test_relative_acceptance_checks_and_stage_runner(tmp_path: Path) -> None:
    m4_1 = tmp_path / "M4.1"
    m4_1.mkdir()
    for filename in (
        "M4_1_PASS",
        "m4_1_stage_manifest.json",
        "m4_1_acceptance_result.json",
        "m4_1_audit_result.json",
    ):
        (m4_1 / filename).write_text("{}\n", encoding="utf-8")

    checks = relative_acceptance_checks(m4_1_dir=m4_1)
    assert not [check for check in checks if check["status"] == "FAIL"]

    result = run_m4_stage(
        Path("configs/m4/m4_skeleton.yaml"),
        stage_id="M4.2",
        output_dir=tmp_path / "out",
        m4_1_dir=m4_1,
    )
    assert result["status"] == "PASS"
    stage_dir = Path(str(result["stage_dir"]))
    assert (stage_dir / "M4_2_PASS").is_file()
    assert not any(stage_dir.glob("*.pt"))
    assert not any(stage_dir.glob("*.parquet"))


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_relative_cpu_cuda_parity() -> None:
    torch.manual_seed(20260727)
    cpu = RelativePositionModule()
    cpu.eval()
    cuda = RelativePositionModule().cuda()
    cuda.load_state_dict(cpu.state_dict())
    cuda.eval()

    relative_xy = torch.tensor([[[2.0, 3.0], [5.0, 7.0]]], dtype=torch.float32)
    mask = torch.tensor([[True, True]])
    cpu_output = cpu(relative_xy, object_mask=mask)
    cuda_output = cuda(relative_xy.cuda(), object_mask=mask.cuda())

    assert torch.allclose(cpu_output.relative_code, cuda_output.relative_code.cpu(), atol=1.0e-5, rtol=1.0e-5)
    assert torch.allclose(cpu_output.e_rel, cuda_output.e_rel.cpu(), atol=1.0e-4, rtol=1.0e-4)
