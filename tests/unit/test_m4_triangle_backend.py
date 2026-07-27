from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from shapely.geometry import MultiPolygon, Polygon

from scene.m4.polygon_errors import GeometryPrimitiveError
from scene.m4.polygon_fourier import polygon_fourier_transform
from scene.m4.triangle_backend import (
    TRIANGLE_OPTIONS,
    polygon_to_pslg,
    triangle_dependency_info,
    triangulate_polygon_domain,
)
from scene.m4.workflow import run_m4_stage


def test_triangle_dependency_status_is_explicit() -> None:
    info = triangle_dependency_info()
    assert info.options == TRIANGLE_OPTIONS
    assert isinstance(info.import_ok, bool)
    if info.import_ok:
        assert info.version
        assert "triangle" in (info.module_path or "")
    else:
        assert info.error


@pytest.mark.skipif(not triangle_dependency_info().import_ok, reason="triangle unavailable")
def test_triangle_pslg_preserves_hole_seed_and_segments() -> None:
    polygon = Polygon(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
        holes=[[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]],
    )
    pslg = polygon_to_pslg(polygon)

    assert pslg["vertices"].shape == (8, 2)
    assert pslg["segments"].shape == (8, 2)
    assert pslg["holes"].shape == (1, 2)
    assert Polygon(polygon.interiors[0]).contains(Polygon([(1, 1), (3, 1), (3, 3), (1, 3)]).representative_point())


@pytest.mark.skipif(not triangle_dependency_info().import_ok, reason="triangle unavailable")
def test_triangle_backend_polygon_domain_preservation() -> None:
    polygon = Polygon(
        [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)],
        holes=[[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]],
    )
    triangles = triangulate_polygon_domain(polygon)
    area = sum(triangle.area for triangle in triangles)

    assert len(triangles) > 0
    assert area == pytest.approx(polygon.area, abs=1.0e-6)
    assert all(polygon.covers(triangle.representative_point()) for triangle in triangles)


@pytest.mark.skipif(not triangle_dependency_info().import_ok, reason="triangle unavailable")
def test_triangle_backend_merges_exact_shared_shell_hole_vertex() -> None:
    polygon = Polygon(
        [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (4.0, 4.0), (0.0, 4.0)],
        holes=[[(4.0, 2.0), (3.0, 1.0), (3.0, 3.0)]],
    )
    pslg = polygon_to_pslg(polygon)
    triangles = triangulate_polygon_domain(polygon)

    assert polygon.is_valid
    assert pslg["vertices"].shape == (7, 2)
    assert pslg["segments"].shape == (8, 2)
    assert sum(triangle.area for triangle in triangles) == pytest.approx(polygon.area, abs=1.0e-6)
    assert all(polygon.covers(triangle.representative_point()) for triangle in triangles)


@pytest.mark.skipif(not triangle_dependency_info().import_ok, reason="triangle unavailable")
def test_triangle_backend_multipolygon_and_no_repair() -> None:
    first = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    second = Polygon([(3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0)])
    triangles = triangulate_polygon_domain(MultiPolygon([second, first]))

    assert sum(triangle.area for triangle in triangles) == pytest.approx(2.0, abs=1.0e-6)
    with pytest.raises(GeometryPrimitiveError):
        triangulate_polygon_domain(Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]))


@pytest.mark.skipif(not triangle_dependency_info().import_ok, reason="triangle unavailable")
def test_polygon_fourier_uses_triangle_backend() -> None:
    polygon = Polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    omega = torch.zeros((1, 2), dtype=torch.float64)
    value = polygon_fourier_transform(polygon, omega, dtype=torch.float64)
    assert value.real.item() == pytest.approx(4.0, abs=1.0e-10)


def test_m4_3a_stage_runner_requires_m4_3_and_never_passes_without_dependency(tmp_path: Path) -> None:
    m4_3 = tmp_path / "M4.3"
    m4_3.mkdir()
    for filename in (
        "M4_3_PASS",
        "m4_3_stage_manifest.json",
        "m4_3_acceptance_result.json",
        "m4_3_audit_result.json",
    ):
        (m4_3 / filename).write_text("{}\n", encoding="utf-8")

    result = run_m4_stage(
        Path("configs/m4/m4_skeleton.yaml"),
        stage_id="M4.3A",
        output_dir=tmp_path / "out",
        m4_3_dir=m4_3,
        workers=40,
    )
    stage_dir = Path(str(result["stage_dir"]))
    manifest = json.loads((stage_dir / "m4_3a_stage_manifest.json").read_text())
    acceptance = json.loads((stage_dir / "m4_3a_acceptance_result.json").read_text())

    assert manifest["auto_continue"] is False
    assert not any(stage_dir.glob("*.pt"))
    assert not any(stage_dir.glob("*.parquet"))
    if triangle_dependency_info().import_ok:
        assert acceptance["status"] in {"PASS", "FAIL"}
    else:
        assert result["status"] == "FAIL"
        assert not (stage_dir / "M4_3A_PASS").exists()
        assert (stage_dir / "M4_3A_FAIL").is_file()
