"""GeoPackage, Zstandard Parquet, and JSON M1.7 serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.core.run_context import RunMetadata
from scene.scenes.allowable_region import SPLITS
from scene.scenes.models import SceneGenerationResult


@dataclass(frozen=True, slots=True)
class SceneArtifacts:
    scene_footprints_gpkg: Path
    scene_footprints_parquet: Path
    scene_district_mapping_parquet: Path
    split_allowable_regions_gpkg: Path
    scene_generation_summary_json: Path
    scene_validation_json: Path
    provenance_parquet: Path

    def to_dict(self) -> dict[str, str]:
        return {
            field: str(getattr(self, field))
            for field in self.__dataclass_fields__
        }


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_scene_artifacts(
    result: SceneGenerationResult,
    validation: dict[str, Any],
    summary: dict[str, Any],
    output_directory: Path,
    metadata: RunMetadata,
    *,
    canonical_boundary_hash: str,
) -> SceneArtifacts:
    output_directory.mkdir(parents=True, exist_ok=False)
    scene_gpkg = output_directory / "scene_footprints.gpkg"
    scene_parquet = output_directory / "scene_footprints.parquet"
    mapping_parquet = output_directory / "scene_district_mapping.parquet"
    allowable_gpkg = output_directory / "split_allowable_regions.gpkg"
    summary_json = output_directory / "scene_generation_summary.json"
    validation_json = output_directory / "scene_validation.json"
    provenance_parquet = output_directory / "provenance.parquet"

    pyogrio.write_dataframe(
        result.scenes,
        scene_gpkg,
        layer="scene_footprints",
        driver="GPKG",
    )
    attributes = result.scenes.drop(columns="geometry")
    pq.write_table(
        pa.Table.from_pandas(attributes, preserve_index=False),
        scene_parquet,
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pandas(
            result.district_mapping,
            preserve_index=False,
        ),
        mapping_parquet,
        compression="zstd",
    )
    raw_frame = gpd.GeoDataFrame(
        [
            {"split": split, "geometry": result.allowable_regions.raw_unions[split]}
            for split in SPLITS
        ],
        crs=result.scenes.crs,
    )
    pyogrio.write_dataframe(
        raw_frame,
        allowable_gpkg,
        layer="raw_split_unions",
        driver="GPKG",
    )
    pyogrio.write_dataframe(
        result.allowable_frame,
        allowable_gpkg,
        layer="split_allowable_regions",
        driver="GPKG",
        append=True,
    )
    provenance = pa.Table.from_pydict(
        {
            "scene_footprint_id": result.scenes[
                "scene_footprint_id"
            ].astype(str).to_list(),
            "scene_generation_version": result.scenes[
                "scene_generation_version"
            ].astype(str).to_list(),
            "assignment_version": result.scenes[
                "assignment_version"
            ].astype(str).to_list(),
            "assignment_hash": result.scenes[
                "assignment_hash"
            ].astype(str).to_list(),
            "canonical_boundary_hash": [canonical_boundary_hash]
            * len(result.scenes),
            "scene_generation_config_hash": [
                result.scene_generation_config_hash
            ]
            * len(result.scenes),
            "run_id": [metadata.run_id] * len(result.scenes),
            "shapely_version": [metadata.shapely_version]
            * len(result.scenes),
            "geos_version": [metadata.geos_version]
            * len(result.scenes),
        }
    )
    pq.write_table(provenance, provenance_parquet, compression="zstd")
    write_json(summary_json, summary)
    write_json(validation_json, validation)
    return SceneArtifacts(
        scene_footprints_gpkg=scene_gpkg,
        scene_footprints_parquet=scene_parquet,
        scene_district_mapping_parquet=mapping_parquet,
        split_allowable_regions_gpkg=allowable_gpkg,
        scene_generation_summary_json=summary_json,
        scene_validation_json=validation_json,
        provenance_parquet=provenance_parquet,
    )
