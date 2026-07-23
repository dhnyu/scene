"""Read-only discovery and extraction of official Korean boundaries."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pyogrio

from scene.boundaries.exceptions import BoundaryIntegrationError
from scene.boundaries.metadata import BoundarySourceAudit, LayerAudit
from scene.inventory.hashing import sha256_file


DISTRICT_FIELDS = ("SIGUNGU_CD", "SIGUNGU_NM")
SIDO_FIELDS = ("SIDO_CD", "SIDO_NM")


def audit_boundary_source(path: str | Path) -> BoundarySourceAudit:
    """Inspect every layer and select only layers with observed official fields."""

    source_path = Path(path).expanduser().resolve(strict=False)
    if not source_path.is_file():
        raise BoundaryIntegrationError(
            f"administrative boundary source is missing: {source_path}"
        )
    try:
        with source_path.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise BoundaryIntegrationError(
            f"administrative boundary source is unreadable: {exc}"
        ) from exc

    layers: list[LayerAudit] = []
    district_candidates: list[str] = []
    sido_candidates: list[str] = []
    try:
        layer_rows = pyogrio.list_layers(source_path)
        for layer_value, _ in layer_rows:
            layer = str(layer_value)
            info = pyogrio.read_info(
                source_path,
                layer=layer,
                force_feature_count=True,
                force_total_bounds=True,
            )
            fields = tuple(str(value) for value in info.get("fields", ()))
            if set(DISTRICT_FIELDS).issubset(fields):
                district_candidates.append(layer)
            if set(SIDO_FIELDS).issubset(fields):
                sido_candidates.append(layer)
            raw_bounds = info.get("total_bounds")
            bbox = (
                tuple(float(value) for value in raw_bounds)
                if raw_bounds is not None
                else None
            )
            layers.append(
                LayerAudit(
                    layer_name=layer,
                    row_count=int(info.get("features", -1)),
                    crs=(
                        str(info.get("crs"))
                        if info.get("crs") is not None
                        else None
                    ),
                    geometry_type=(
                        str(info.get("geometry_type"))
                        if info.get("geometry_type") is not None
                        else None
                    ),
                    fields=fields,
                    bbox=bbox,
                )
            )
    except Exception as exc:
        raise BoundaryIntegrationError(
            f"cannot inspect administrative boundary GeoPackage: {exc}"
        ) from exc

    if len(district_candidates) != 1 or len(sido_candidates) != 1:
        raise BoundaryIntegrationError(
            "official district/province layers are ambiguous: "
            f"district={district_candidates}, sido={sido_candidates}"
        )
    stat = source_path.stat()
    return BoundarySourceAudit(
        source_path=str(source_path),
        exists=True,
        readable=True,
        file_size=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        sha256=sha256_file(source_path),
        layers=tuple(layers),
        district_layer=district_candidates[0],
        sido_layer=sido_candidates[0],
        district_code_field=DISTRICT_FIELDS[0],
        district_name_field=DISTRICT_FIELDS[1],
        sido_code_field=SIDO_FIELDS[0],
        sido_name_field=SIDO_FIELDS[1],
    )


def read_seoul_features(
    audit: BoundarySourceAudit,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Filter Seoul by official code while preserving source FIDs."""

    path = Path(audit.source_path)
    districts = pyogrio.read_dataframe(
        path,
        layer=audit.district_layer,
        fid_as_index=True,
    )
    sido = pyogrio.read_dataframe(
        path,
        layer=audit.sido_layer,
        fid_as_index=True,
    )
    district_codes = districts[audit.district_code_field].astype("string")
    sido_codes = sido[audit.sido_code_field].astype("string")
    seoul_districts = districts.loc[district_codes.str.startswith("11")].copy()
    seoul = sido.loc[sido_codes == "11"].copy()
    if len(seoul_districts) != 25:
        raise BoundaryIntegrationError(
            f"Seoul district filter returned {len(seoul_districts)}, expected 25"
        )
    if len(seoul) != 1:
        raise BoundaryIntegrationError(
            f"Seoul sido filter returned {len(seoul)}, expected 1"
        )
    seoul_districts["source_fid"] = seoul_districts.index.astype("int64")
    seoul["source_fid"] = seoul.index.astype("int64")
    return seoul_districts.reset_index(drop=True), seoul.reset_index(drop=True)
