from __future__ import annotations

from pathlib import Path

from scene.core.config import load_config
from scene.split.assign import build_assignment
from scene.split.balancing import prepare_balance_model, search_assignment
from scene.split.statistics import (
    compute_balancing_statistics,
    load_canonical_districts,
)
from scene.split.validator import validate_assignment


def test_actual_canonical_district_assignment_is_reproducible() -> None:
    root = Path(__file__).resolve().parents[2]
    project = load_config(root / "configs/project.yaml")
    config = project.district_assignment
    assert config is not None
    canonical = load_canonical_districts(config)
    statistics = compute_balancing_statistics(project, canonical)
    model = prepare_balance_model(statistics, canonical.districts, config)
    first = search_assignment(statistics, model, config)
    second = search_assignment(statistics, model, config)
    assignment = build_assignment(
        canonical,
        model,
        first,
        config,
        run_id="20260724_120000_KST",
    )
    regenerated = build_assignment(
        canonical,
        model,
        second,
        config,
        run_id="20260724_120001_KST",
    )
    validation = validate_assignment(
        assignment,
        config,
        regenerated_assignment=regenerated,
    )

    assert validation.valid
    assert validation.train_count == 15
    assert validation.validation_count == 5
    assert validation.test_count == 5
    assert statistics.landcover_codes
    assert statistics.poi_categories
    assert first.feasible_candidate_count > 0
    assert (
        first.connected_component_count_by_split["validation"] in {2, 3}
    )
    assert first.connected_component_count_by_split["test"] in {2, 3}
