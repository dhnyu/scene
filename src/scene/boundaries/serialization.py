"""D-011A boundary, provenance, and mapping serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely import to_wkb

from scene.boundaries.metadata import BoundaryValidation, CanonicalDistricts
from scene.boundaries.provenance import canonical_geometry_sha256
from scene.inventory.hashing import sha256_file


@dataclass(frozen=True, slots=True)
class BoundaryArtifacts:
    geopackage: Path
    metadata_json: Path
    validation_json: Path
    districts_parquet: Path
    spatial_json: Path
    provenance_parquet: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in self.__dataclass_fields__
        }


def _json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _table(dataset: CanonicalDistricts) -> pa.Table:
    districts = dataset.districts
    data = {
        column: districts[column].to_list()
        for column in districts.columns
        if column != districts.geometry.name
    }
    data["geometry_wkb"] = [
        to_wkb(geometry, byte_order=1, output_dimension=2)
        for geometry in districts.geometry
    ]
    return pa.Table.from_pydict(data)


def write_boundary_artifacts(
    dataset: CanonicalDistricts,
    validation: BoundaryValidation,
    spatial: Mapping[str, object],
    directory: str | Path,
    *,
    run_id: str,
    config_hash: str,
    canonical_schema_version: str,
) -> BoundaryArtifacts:
    """Write a new run directory; never overwrite an existing canonical layer."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=False)
    geopackage = output / "seoul_administrative_boundaries.gpkg"
    pyogrio_kwargs = {"driver": "GPKG", "promote_to_multi": True}
    dataset.seoul.to_file(
        geopackage,
        layer="seoul_sido",
        engine="pyogrio",
        **pyogrio_kwargs,
    )
    dataset.districts.to_file(
        geopackage,
        layer="seoul_sigungu",
        engine="pyogrio",
        mode="a",
        **pyogrio_kwargs,
    )

    district_table = _table(dataset)
    district_path = output / "seoul_districts.parquet"
    pq.write_table(district_table, district_path, compression="zstd")
    provenance = pa.Table.from_pydict(
        {
            "district_id": dataset.districts["district_id"].to_list(),
            "source_name": dataset.districts["source_name"].to_list(),
            "source_object_id": dataset.districts[
                "source_object_id"
            ].to_list(),
            "source_path": dataset.districts["source_path"].to_list(),
            "source_layer": dataset.districts["source_layer"].to_list(),
            "source_fid": dataset.districts["source_fid"].to_list(),
            "source_sha256": dataset.districts["source_sha256"].to_list(),
            "source_crs": dataset.districts["source_crs"].to_list(),
            "canonical_crs": dataset.districts["canonical_crs"].to_list(),
            "canonical_geometry_sha256": [
                canonical_geometry_sha256(geometry)
                for geometry in dataset.districts.geometry
            ],
            "canonical_schema_version": [
                canonical_schema_version
            ] * len(dataset.districts),
            "run_id": [run_id] * len(dataset.districts),
            "config_hash": [config_hash] * len(dataset.districts),
        }
    )
    provenance_path = output / "provenance.parquet"
    pq.write_table(provenance, provenance_path, compression="zstd")
    metadata_path = output / "seoul_district_metadata.json"
    validation_path = output / "seoul_district_validation.json"
    spatial_path = output / "spatial_consistency.json"
    _json(
        metadata_path,
        {
            "canonical_content_hash": dataset.content_hash,
            "canonical_crs": "EPSG:5186",
            "canonical_geopackage": str(geopackage),
            "canonical_geopackage_sha256": sha256_file(geopackage),
            "district_codes": dataset.districts["district_code"].to_list(),
            "district_names": dataset.districts["district_name"].to_list(),
            "feature_count": len(dataset.districts),
            "layer": "seoul_sigungu",
            "run_id": run_id,
            "source_audit": dataset.source_audit.to_dict(),
            "stable_id_method": (
                "SHA256(length-prefixed UTF-8: district_id, source_name, "
                "administrative_level, district_code)"
            ),
        },
    )
    _json(validation_path, validation.to_dict())
    _json(spatial_path, dict(spatial))
    return BoundaryArtifacts(
        geopackage=geopackage,
        metadata_json=metadata_path,
        validation_json=validation_path,
        districts_parquet=district_path,
        spatial_json=spatial_path,
        provenance_parquet=provenance_path,
    )
