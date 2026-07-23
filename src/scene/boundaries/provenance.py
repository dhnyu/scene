"""Deterministic geometry and mapping provenance helpers."""

from __future__ import annotations

import hashlib

import geopandas as gpd
from shapely import normalize, to_wkb

from scene.id.generator import canonical_hash


def canonical_geometry_sha256(geometry: object) -> str:
    """Hash normalized two-dimensional little-endian WKB."""

    payload = to_wkb(
        normalize(geometry),
        byte_order=1,
        output_dimension=2,
        include_srid=False,
    )
    return hashlib.sha256(payload).hexdigest()


def district_content_hash(districts: gpd.GeoDataFrame) -> str:
    """Hash sorted official attributes and normalized geometry fingerprints."""

    rows = sorted(
        (
            str(row.district_code),
            str(row.district_name),
            str(row.sido_code),
            str(row.sido_name),
            str(row.district_id),
            canonical_geometry_sha256(row.geometry),
        )
        for row in districts.itertuples()
    )
    return canonical_hash(
        "seoul_district_content",
        *(value for row in rows for value in row),
    )
