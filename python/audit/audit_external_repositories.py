#!/usr/bin/env python
"""Inspect local external repositories without importing or modifying them."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from audit_utils import (
    load_configs,
    now_kst,
    raw_dir,
    run_command,
    setup_logging,
    write_csv,
    write_json,
)

LOGGER = setup_logging("external_code")


def git_value(repo: Path, arguments: list[str]) -> str | None:
    result = run_command(["git", "-C", str(repo), *arguments])
    if result["returncode"] == 0:
        return result["stdout"].strip()
    return None


def discover_license(repo: Path) -> dict[str, Any]:
    candidates = sorted(
        path for path in repo.iterdir()
        if path.is_file() and path.name.lower().startswith(("license", "copying"))
    )
    if not candidates:
        return {"path": None, "spdx_candidate": "미확정"}
    path = candidates[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    if "MIT License" in text:
        spdx = "MIT"
    elif "Apache License" in text:
        spdx = "Apache-2.0"
    elif "GNU GENERAL PUBLIC LICENSE" in text.upper():
        spdx = "GPL (version 추가 확인 필요)"
    else:
        spdx = "미확정"
    return {"path": str(path), "spdx_candidate": spdx}


def ast_symbols(path: Path) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"symbol": None, "kind": "parse_error", "line": None,
                 "error": f"{type(exc).__name__}: {exc}"}]
    rows: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            rows.append({
                "symbol": node.name, "kind": "class", "line": node.lineno, "error": None,
            })
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    rows.append({
                        "symbol": f"{node.name}.{child.name}",
                        "kind": "method",
                        "line": child.lineno,
                        "error": None,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append({
                "symbol": node.name, "kind": "function", "line": node.lineno, "error": None,
            })
    return sorted(rows, key=lambda row: (row["line"] or 0, row["symbol"] or ""))


def file_matches(repo: Path, patterns: list[str]) -> list[str]:
    regex = re.compile("|".join(patterns), flags=re.IGNORECASE)
    result = []
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.parts and regex.search(path.name):
            result.append(str(path))
    return sorted(result)


def repo_inventory(repo: Path) -> dict[str, Any]:
    is_git = (repo / ".git").exists()
    tracked_status = git_value(repo, ["status", "--short"]) if is_git else None
    readmes = file_matches(repo, [r"^readme"])
    requirements = file_matches(
        repo, [r"requirements.*\.txt$", r"environment.*\.ya?ml$", r"pyproject\.toml$",
               r"setup\.py$"]
    )
    configs = file_matches(repo, [r"config", r"\.ya?ml$", r"\.json$"])
    tests = sorted(
        str(path) for path in repo.rglob("*.py")
        if ".git" not in path.parts
        and (
            path.name.startswith("test")
            or path.name.endswith("_test.py")
            or any(part.lower() in {"test", "tests"} for part in path.parts)
        )
    )
    examples = sorted(
        str(path) for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.parts
        and re.search(r"example|tutorial", path.name, flags=re.IGNORECASE)
        and path.suffix.lower() in {".py", ".ipynb", ".md", ".rst"}
    )
    weights = [
        str(path) for path in repo.rglob("*")
        if path.is_file() and (
            path.suffix.lower() in {".pt", ".pth", ".ckpt"}
            or ".pth." in path.name.lower()
            or ".ckpt." in path.name.lower()
        )
        and ".git" not in path.parts
    ]
    python_files = [
        path for path in repo.rglob("*.py") if ".git" not in path.parts
    ]
    configuration_validation = []
    for candidate in [repo / "config.json"]:
        if candidate.exists():
            try:
                with candidate.open("r", encoding="utf-8") as stream:
                    json.load(stream)
                configuration_validation.append({
                    "path": str(candidate), "valid_json": True, "error": None,
                })
            except Exception as exc:
                configuration_validation.append({
                    "path": str(candidate), "valid_json": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    symbols = []
    for path in python_files:
        for symbol in ast_symbols(path):
            symbols.append({
                "repository": repo.name,
                "source_path": str(path),
                **symbol,
            })
    latest = git_value(repo, ["log", "-1", "--format=%cI%x09%h%x09%s"]) if is_git else None
    return {
        "repository": repo.name,
        "absolute_path": str(repo.resolve()),
        "is_git": is_git,
        "branch": git_value(repo, ["branch", "--show-current"]) if is_git else None,
        "commit": git_value(repo, ["rev-parse", "HEAD"]) if is_git else None,
        "remote_urls": (git_value(repo, ["remote", "-v"]) or "").splitlines() if is_git else [],
        "working_tree_clean": tracked_status == "",
        "working_tree_status": (tracked_status or "").splitlines(),
        "latest_local_commit": latest,
        "license": discover_license(repo),
        "readmes": readmes,
        "dependency_files": requirements,
        "configuration_files": configs,
        "configuration_validation": configuration_validation,
        "test_files": tests,
        "example_files": examples,
        "pretrained_weight_files": sorted(weights),
        "python_file_count": len(python_files),
        "symbols": symbols,
    }


def component_rows(external_root: Path) -> list[dict[str, Any]]:
    """Create verified component references for the two target repositories."""
    specs = [
        ("poly2vec", "models/fourier_encoder.py", "GeometryFourierEncoder",
         "Fourier geometry encoder", "class"),
        ("poly2vec", "models/fourier_encoder.py", "GeometryFourierEncoder.encode",
         "geometry-type dispatch and batch input", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.create_gfm_meshgrid",
         "geometric frequency grid", "method"),
        ("poly2vec", "models/fourier_encoder.py", "GeometryFourierEncoder.point_encoder",
         "point continuous FT", "method"),
        ("poly2vec", "models/fourier_encoder.py", "GeometryFourierEncoder.line_encoder",
         "line segment continuous FT", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.polyline_encoder",
         "polyline segment summation", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.polygon_encoder",
         "polygon batch entry", "method"),
        ("poly2vec", "models/fourier_encoder.py", "GeometryFourierEncoder.polygon_ft",
         "polygon triangulation and FT", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.preprocess_polygon",
         "buffer(0) geometry repair", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.cdt_triangulate",
         "Triangle constrained triangulation", "method"),
        ("poly2vec", "models/fourier_encoder.py",
         "GeometryFourierEncoder.fourier_transform_rtriangle",
         "triangle FT and zero-frequency branches", "method"),
        ("poly2vec", "models/poly2vec.py", "Poly2Vec",
         "magnitude/phase encoders and fusion", "class"),
        ("poly2vec", "models/poly2vec.py", "Poly2Vec.forward",
         "embedding inference", "method"),
        ("torchspatial", "main/module.py", "PositionEncoder",
         "common position encoder interface", "class"),
        ("torchspatial", "main/module.py", "LocationEncoder",
         "common location encoder interface", "class"),
        ("torchspatial", "main/module.py", "MultiLayerFeedForwardNN",
         "shared MLP", "class"),
        ("torchspatial", "main/SpatialRelationEncoder.py", "_cal_freq_list",
         "frequency/wavelength construction", "function"),
        ("torchspatial", "main/SpatialRelationEncoder.py",
         "GridCellSpatialRelationPositionEncoder",
         "Space2Vec-grid position encoding", "class"),
        ("torchspatial", "main/SpatialRelationEncoder.py",
         "GridCellSpatialRelationLocationEncoder",
         "Space2Vec-grid location encoding plus MLP", "class"),
        ("torchspatial", "main/SpatialRelationEncoder.py",
         "TheoryGridCellSpatialRelationPositionEncoder",
         "Space2Vec-theory 3-direction encoding", "class"),
        ("torchspatial", "main/SpatialRelationEncoder.py",
         "TheoryGridCellSpatialRelationLocationEncoder",
         "Space2Vec-theory location encoding plus MLP", "class"),
        ("torchspatial", "main/SpatialRelationEncoder.py",
         "TheoryDiagGridCellSpatialRelationEncoder",
         "theorydiag variant", "class"),
        ("torchspatial", "main/utils.py", "get_spa_enc_list",
         "registered encoder names", "function"),
        ("torchspatial", "main/utils.py", "generate_model_input_feats",
         "normalization routing", "function"),
        ("torchspatial", "main/utils.py", "convert_loc_to_tensor_no_normalize",
         "non-normalized lon/lat tensor conversion", "function"),
        ("torchspatial", "main/utils.py", "get_spa_encoder",
         "encoder factory and configuration", "function"),
    ]
    rows = []
    cache: dict[Path, list[dict[str, Any]]] = {}
    for repository, relative, symbol, role, kind in specs:
        path = external_root / repository / relative
        cache.setdefault(path, ast_symbols(path) if path.exists() else [])
        hits = [row for row in cache[path] if row.get("symbol") == symbol]
        rows.append({
            "checked_at_kst": now_kst(),
            "repository": repository,
            "source_path": str(path),
            "symbol": symbol,
            "symbol_kind": kind,
            "line": hits[0]["line"] if hits else None,
            "role": role,
            "verified": bool(hits),
        })
    return rows


def main() -> int:
    root, timestamp, paths, _, audit = load_configs()
    external_root = Path(paths["external_root"])
    out_dir = raw_dir(root, timestamp)
    requested = audit.get("external_repositories", [])
    repositories = []
    errors = []
    for name in requested:
        path = external_root / name
        if not path.exists():
            errors.append({"repository": name, "error": "경로 없음"})
            continue
        LOGGER.info("Inspecting external repository: %s", path)
        try:
            repositories.append(repo_inventory(path))
        except Exception as exc:
            errors.append({"repository": name, "error": f"{type(exc).__name__}: {exc}"})
    components = component_rows(external_root)
    result = {
        "checked_at_kst": now_kst(),
        "external_root": str(external_root),
        "repositories": repositories,
        "component_inventory": components,
        "errors": errors,
    }
    write_json(out_dir / "external_code_audit.json", result)
    write_csv(
        root / "metadata" / f"{timestamp}_external_code_inventory.csv",
        components,
        ["checked_at_kst", "repository", "source_path", "symbol", "symbol_kind",
         "line", "role", "verified"],
    )
    LOGGER.info("External repository audit complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
