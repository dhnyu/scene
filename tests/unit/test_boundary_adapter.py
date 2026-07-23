from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely import MultiPolygon, Polygon

from scene.boundaries.adapter import adapt_seoul_districts
from scene.boundaries.provenance import district_content_hash
from scene.boundaries.reader import audit_boundary_source, read_seoul_features
from scene.boundaries.validator import validate_canonical_districts


def _write_source(path: Path) -> Path:
    polygons = [
        MultiPolygon(
            [
                Polygon(
                    [
                        (950000 + index * 100, 1950000),
                        (950100 + index * 100, 1950000),
                        (950100 + index * 100, 1950100),
                        (950000 + index * 100, 1950100),
                    ]
                )
            ]
        )
        for index in range(25)
    ]
    districts = gpd.GeoDataFrame(
        {
            "BASE_DATE": ["20240630"] * 25,
            "SIGUNGU_NM": [f"구{index:02d}" for index in range(25)],
            "SIGUNGU_CD": [f"11{index:03d}" for index in range(25)],
        },
        geometry=polygons,
        crs="EPSG:5179",
    )
    sido = gpd.GeoDataFrame(
        {"BASE_DATE": ["20240630"], "SIDO_CD": ["11"], "SIDO_NM": ["서울특별시"]},
        geometry=[MultiPolygon([Polygon([(949900, 1949900), (952600, 1949900), (952600, 1950200), (949900, 1950200)])])],
        crs="EPSG:5179",
    )
    sido.to_file(path, layer="sido", driver="GPKG", engine="pyogrio")
    districts.to_file(
        path,
        layer="sigungu",
        driver="GPKG",
        engine="pyogrio",
        mode="a",
    )
    return path


def test_layer_discovery_filter_mapping_and_determinism(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "official.gpkg")
    audit = audit_boundary_source(source)
    districts, sido = read_seoul_features(audit)

    first = adapt_seoul_districts(
        districts,
        sido,
        audit,
        source_name="official_sigungu",
    )
    second = adapt_seoul_districts(
        districts.sample(frac=1, random_state=7),
        sido,
        audit,
        source_name="official_sigungu",
    )
    validation = validate_canonical_districts(first)

    assert audit.district_layer == "sigungu"
    assert audit.sido_layer == "sido"
    assert audit.district_code_field == "SIGUNGU_CD"
    assert len(first.districts) == 25
    assert first.districts.crs.to_epsg() == 5186
    assert validation.valid
    assert first.content_hash == second.content_hash
    assert (
        first.districts.set_index("district_code")["district_id"].to_dict()
        == second.districts.set_index("district_code")["district_id"].to_dict()
    )
    assert district_content_hash(first.districts) == first.content_hash
