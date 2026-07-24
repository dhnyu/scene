from __future__ import annotations

import geopandas as gpd
import pytest
from shapely import box

from scene.scenes.allowable_region import AllowableRegions
from scene.scenes.eligibility import select_eligible_scenes
from scene.scenes.exceptions import SceneFootprintError


def _candidates(geometry) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"grid_col": [0], "grid_row": [0]},
        geometry=[geometry],
        crs="EPSG:5186",
    )


def test_exact_covers_allows_boundary_touch() -> None:
    region = box(0, 0, 1_000, 1_000)
    empty = box(2_000, 2_000, 3_000, 3_000)
    regions = AllowableRegions(
        raw_unions={"train": region, "validation": empty, "test": empty},
        allowable={"train": region, "validation": empty, "test": empty},
        pair_audit=(),
    )
    result = select_eligible_scenes(_candidates(box(0, 0, 500, 500)), regions)
    assert len(result.eligible) == 1
    assert result.eligible.iloc[0]["split"] == "train"


def test_center_only_and_one_nanometre_outside_are_rejected() -> None:
    region = box(0, 0, 1_000, 1_000)
    empty = box(2_000, 2_000, 3_000, 3_000)
    regions = AllowableRegions(
        raw_unions={"train": region, "validation": empty, "test": empty},
        allowable={"train": region, "validation": empty, "test": empty},
        pair_audit=(),
    )
    result = select_eligible_scenes(
        _candidates(box(-1e-9, 0, 500, 500)),
        regions,
    )
    assert result.rejected_count == 1


def test_multi_split_coverage_is_fatal() -> None:
    region = box(0, 0, 1_000, 1_000)
    empty = box(2_000, 2_000, 3_000, 3_000)
    regions = AllowableRegions(
        raw_unions={"train": region, "validation": region, "test": empty},
        allowable={"train": region, "validation": region, "test": empty},
        pair_audit=(),
    )
    with pytest.raises(SceneFootprintError, match="multiple splits"):
        select_eligible_scenes(_candidates(box(0, 0, 500, 500)), regions)
