"""Canonical Seoul district validation."""

from __future__ import annotations

from scene.boundaries.metadata import BoundaryValidation, CanonicalDistricts


def validate_canonical_districts(
    dataset: CanonicalDistricts,
) -> BoundaryValidation:
    districts = dataset.districts
    geometry_null = int(districts.geometry.isna().sum())
    geometry_empty = int(districts.geometry.is_empty.sum())
    geometry_invalid = int((~districts.geometry.is_valid).sum())
    code_null = int(districts["district_code"].isna().sum())
    name_null = int(districts["district_name"].isna().sum())
    id_null = int(districts["district_id"].isna().sum())
    code_duplicate = int(districts["district_code"].duplicated().sum())
    name_duplicate = int(districts["district_name"].duplicated().sum())
    id_duplicate = int(districts["district_id"].duplicated().sum())
    outside = int(
        (~districts["district_code"].astype(str).str.startswith("11")).sum()
    )
    crs = districts.crs.to_string() if districts.crs is not None else None
    geometry_types = tuple(sorted(set(districts.geom_type.astype(str))))
    valid = (
        len(districts) == 25
        and geometry_null == 0
        and geometry_empty == 0
        and geometry_invalid == 0
        and code_null == 0
        and code_duplicate == 0
        and name_null == 0
        and name_duplicate == 0
        and id_null == 0
        and id_duplicate == 0
        and outside == 0
        and crs == "EPSG:5186"
        and set(geometry_types).issubset({"Polygon", "MultiPolygon"})
    )
    return BoundaryValidation(
        row_count=len(districts),
        geometry_null=geometry_null,
        geometry_empty=geometry_empty,
        geometry_invalid=geometry_invalid,
        district_code_null=code_null,
        district_code_duplicate=code_duplicate,
        district_name_null=name_null,
        district_name_duplicate=name_duplicate,
        district_id_null=id_null,
        district_id_duplicate=id_duplicate,
        outside_seoul_code=outside,
        crs=crs,
        geometry_types=geometry_types,
        valid=valid,
    )
