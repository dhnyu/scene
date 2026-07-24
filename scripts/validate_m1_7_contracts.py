#!/usr/bin/env python3
"""Validate the approved M1.7 decision and contract values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import yaml


APPROVED_DECISIONS = ("D-018", "D-019", "D-020", "D-021", "D-022")


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _decision_section(text: str, decision: str) -> str:
    start = text.find(f"### {decision}:")
    if start < 0:
        return ""
    next_section = text.find("\n### D-", start + 1)
    return text[start:] if next_section < 0 else text[start:next_section]


def validate(project_root: Path) -> dict[str, Any]:
    config = yaml.safe_load(
        (project_root / "configs/project.yaml").read_text(encoding="utf-8")
    )
    schema = yaml.safe_load(
        (project_root / "docs/contracts/canonical_schema.yaml").read_text(
            encoding="utf-8"
        )
    )
    decision_text = (
        project_root / "docs/decisions/decision_log.md"
    ).read_text(encoding="utf-8")
    split_text = (
        project_root / "docs/contracts/split_and_scene_contract.md"
    ).read_text(encoding="utf-8")
    id_text = (
        project_root / "docs/contracts/id_and_provenance_contract.md"
    ).read_text(encoding="utf-8")
    acceptance_text = (
        project_root / "docs/contracts/acceptance_tests.md"
    ).read_text(encoding="utf-8")
    allowable_source = (
        project_root / "src/scene/scenes/allowable_region.py"
    ).read_text(encoding="utf-8")

    errors: list[str] = []
    for decision in APPROVED_DECISIONS:
        section = _decision_section(decision_text, decision)
        _require(bool(section), f"{decision} section is missing", errors)
        _require(
            "| 상태 | Approved |" in section,
            f"{decision} is not Approved",
            errors,
        )
        _require(
            decision in acceptance_text,
            f"{decision} acceptance reference is missing",
            errors,
        )

    scene = config.get("scene_generation", {})
    expected = {
        "scene_generation_version": "scene-footprint-v1",
        "canonical_crs": "EPSG:5186",
        "scene_width_m": 500,
        "scene_height_m": 500,
        "stride_x_m": 250,
        "stride_y_m": 250,
        "origin_x_m": 0,
        "origin_y_m": 0,
        "origin_anchor": "center",
        "cross_split_exclusion_per_side_m": 125,
        "minimum_allowable_region_distance_m": 250,
        "eligibility_predicate": "covers",
        "boundary_touch_allowed": True,
        "linear_tolerance_m": 1e-8,
        "area_tolerance_m2": 1e-6,
        "primary_district_rule": "largest_intersection_area",
        "primary_district_tie_break": "district_code_ascending",
    }
    for key, value in expected.items():
        _require(
            scene.get(key) == value,
            f"scene_generation.{key} must equal {value!r}",
            errors,
        )

    validation = schema.get("validation", {})
    identity = validation.get("scene_identity", {})
    _require(
        identity.get("scene_generation_version") == "scene-footprint-v1",
        "canonical schema scene version mismatch",
        errors,
    )
    _require(
        identity.get("grid_anchor") == "center",
        "canonical schema origin anchor mismatch",
        errors,
    )
    _require(
        "split" in identity.get("hash_excludes", []),
        "canonical schema must keep split out of footprint identity",
        errors,
    )
    entities = schema.get("canonical_tables", {})
    _require(
        "scene" in entities,
        "canonical scene_footprint entity is missing",
        errors,
    )
    _require(
        "scene_district_mapping" in entities,
        "canonical scene_district_mapping entity is missing",
        errors,
    )

    required_split_literals = (
        "500 m",
        "250 m",
        "125 m",
        "`covers`",
        "`(0, 0)`",
        "`1e-8 m`",
        "`1e-6 m²`",
        "district_code",
    )
    for literal in required_split_literals:
        _require(
            literal in split_text,
            f"split contract is missing {literal}",
            errors,
        )
    _require(
        "union_A - buffer(union_B union union_C, 125 m)" in split_text,
        "split contract is missing the strict D-019 formula",
        errors,
    )
    _require(
        allowable_source.count("shapely.buffer(") == 1,
        "D-019 implementation must contain exactly one buffer operation",
        errors,
    )
    for prohibited in (
        "computational_radius",
        "quad_segments",
        "corner_refinement",
        "proximity_refinement",
    ):
        _require(
            prohibited not in allowable_source,
            f"D-019 implementation contains prohibited {prohibited}",
            errors,
        )
    for literal in (
        "scene-footprint-v1",
        "grid_col",
        "grid_row",
        "split",
        "run ID",
        "timestamp",
    ):
        _require(
            literal in id_text,
            f"ID contract is missing {literal}",
            errors,
        )

    return {
        "approved_decisions": list(APPROVED_DECISIONS),
        "error_count": len(errors),
        "errors": errors,
        "status": "pass" if not errors else "fail",
        "values": expected,
        "d019_strict_formula": "pass" if not errors else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    result = validate(args.project_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
