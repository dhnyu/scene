from __future__ import annotations

import geopandas as gpd
import shapely
from shapely import box

from scene.core.config import load_config
from scene.scenes.allowable_region import build_allowable_regions


def _districts(separated: bool = False) -> gpd.GeoDataFrame:
    validation_min = 2_500.0 if separated else 2_000.0
    return gpd.GeoDataFrame(
        {
            "district_id": ["a", "b", "c", "d"],
            "split": ["train", "train", "validation", "test"],
        },
        geometry=[
            box(0, 0, 1_000, 1_000),
            box(1_000, 0, 2_000, 1_000),
            box(validation_min, 0, validation_min + 1_000, 1_000),
            box(0, 2_500, 1_000, 3_500),
        ],
        crs="EPSG:5186",
    )


def test_same_split_boundary_and_outer_boundary_are_preserved(
    project_config_path,
) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    regions = build_allowable_regions(_districts(), config)
    train = regions.allowable["train"]
    assert train.covers(box(900, 100, 1_100, 900))
    assert train.bounds[0] == 0.0
    assert train.bounds[1] == 0.0
    assert regions.allowable["validation"].bounds[2] == 3_000.0


def test_allowable_geometry_equals_approved_formula(
    project_config_path,
) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    districts = _districts()
    regions = build_allowable_regions(districts, config)
    raw = {
        split: shapely.union_all(
            districts.loc[districts["split"] == split].geometry.array
        )
        for split in ("train", "validation", "test")
    }
    for split in raw:
        expected = shapely.difference(
            raw[split],
            shapely.buffer(
                shapely.union_all(
                    [
                        geometry
                        for other, geometry in raw.items()
                        if other != split
                    ]
                ),
                125.0,
            ),
        )
        assert shapely.equals(regions.allowable[split], expected)


def test_no_second_exclusion_or_refinement(
    project_config_path,
    monkeypatch,
) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    original = shapely.buffer
    calls: list[float] = []

    def recording_buffer(geometry, distance, *args, **kwargs):
        calls.append(float(distance))
        return original(geometry, distance, *args, **kwargs)

    monkeypatch.setattr(shapely, "buffer", recording_buffer)
    build_allowable_regions(_districts(), config)
    assert calls == [125.0, 125.0, 125.0]


def test_no_unneeded_exclusion_for_separated_splits(
    project_config_path,
) -> None:
    config = load_config(project_config_path).scene_generation
    assert config is not None
    separated = build_allowable_regions(_districts(separated=True), config)
    assert separated.allowable["train"].bounds[2] == 2_000.0
    assert separated.allowable["validation"].bounds[0] == 2_500.0
