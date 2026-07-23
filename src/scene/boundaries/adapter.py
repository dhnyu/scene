"""Official Seoul district source-to-canonical adapter."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from scene.boundaries.exceptions import BoundaryIntegrationError
from scene.boundaries.metadata import BoundarySourceAudit, CanonicalDistricts
from scene.boundaries.provenance import district_content_hash
from scene.id.generator import district_id


CANONICAL_CRS = "EPSG:5186"
ADMINISTRATIVE_LEVEL = "sigungu"


def adapt_seoul_districts(
    source_districts: gpd.GeoDataFrame,
    source_seoul: gpd.GeoDataFrame,
    audit: BoundarySourceAudit,
    *,
    source_name: str,
) -> CanonicalDistricts:
    """Map observed official fields and perform a true coordinate transform."""

    if source_districts.crs is None or source_seoul.crs is None:
        raise BoundaryIntegrationError("official source CRS is missing")
    source_crs = source_districts.crs.to_string()
    districts = source_districts.to_crs(CANONICAL_CRS)
    seoul = source_seoul.to_crs(CANONICAL_CRS)
    district_code = districts[audit.district_code_field].astype("string")
    district_name = districts[audit.district_name_field].astype("string")
    sido_code = str(seoul.iloc[0][audit.sido_code_field])
    sido_name = str(seoul.iloc[0][audit.sido_name_field])

    canonical = gpd.GeoDataFrame(
        {
            "district_id": [
                district_id(source_name, ADMINISTRATIVE_LEVEL, str(code))
                for code in district_code
            ],
            "district_code": district_code.astype(str),
            "district_name": district_name.astype(str),
            "sido_code": [sido_code] * len(districts),
            "sido_name": [sido_name] * len(districts),
            "source_name": [source_name] * len(districts),
            "source_object_id": district_code.astype(str),
            "source_layer": [audit.district_layer] * len(districts),
            "source_path": [audit.source_path] * len(districts),
            "source_crs": [source_crs] * len(districts),
            "canonical_crs": [CANONICAL_CRS] * len(districts),
            "source_sha256": [audit.sha256] * len(districts),
            "source_fid": districts["source_fid"].astype("int64"),
            "source_base_date": districts["BASE_DATE"].astype("string").astype(str),
        },
        geometry=districts.geometry.array,
        crs=CANONICAL_CRS,
    ).sort_values("district_code", ignore_index=True)
    canonical_seoul = gpd.GeoDataFrame(
        {
            "sido_code": [sido_code],
            "sido_name": [sido_name],
            "source_name": [source_name],
            "source_layer": [audit.sido_layer],
            "source_path": [audit.source_path],
            "source_crs": [source_seoul.crs.to_string()],
            "canonical_crs": [CANONICAL_CRS],
            "source_sha256": [audit.sha256],
            "source_fid": source_seoul["source_fid"].astype("int64").to_list(),
        },
        geometry=seoul.geometry.array,
        crs=CANONICAL_CRS,
    )
    return CanonicalDistricts(
        districts=canonical,
        seoul=canonical_seoul,
        source_audit=audit,
        content_hash=district_content_hash(canonical),
    )
