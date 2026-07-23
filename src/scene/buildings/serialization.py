"""D-011A serialization for a validated BuildingDataset."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.buildings.dataset import BuildingDataset
from scene.buildings.exceptions import (
    BuildingSerializationError,
    BuildingValidationError,
)
from scene.buildings.validator import BuildingValidationResult
from scene.inventory.hashing import sha256_file


@dataclass(frozen=True, slots=True)
class BuildingArtifactPaths:
    """Materialized BuildingDataset artifacts and hashes."""

    geometry_geopackage: Path
    attribute_parquet: Path
    metadata_json: Path
    geometry_sha256: str
    attribute_sha256: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {key: str(item) for key, item in value.items()}


def _write_geometry(dataset: BuildingDataset, path: Path) -> None:
    temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
    temporary.unlink(missing_ok=True)
    table = dataset.geometry_dataframe.select(
        ["source_building_id", "source_fid", "geometry_wkb"]
    )
    provenance = dataset.geometry.provenance_metadata
    source = dataset.geometry.source_metadata
    try:
        pyogrio.write_arrow(
            table,
            temporary,
            layer="buildings",
            driver="GPKG",
            geometry_name="geometry_wkb",
            geometry_type=dataset.geometry.geometry_type,
            crs=dataset.crs,
            layer_metadata={
                "canonical_frame_sha256": provenance.canonical_frame_sha256,
                "canonical_run_id": provenance.canonical_run_id,
                "canonical_schema_sha256": provenance.canonical_schema_sha256,
                "source_file_sha256": source.source_file_sha256,
                "source_name": source.source_name,
            },
        )
        temporary.replace(path)
    except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise BuildingSerializationError(
            f"cannot write building GeoPackage {path}: {exc}"
        ) from exc


def _write_attributes(dataset: BuildingDataset, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        pq.write_table(
            dataset.attribute_dataframe,
            temporary,
            compression="zstd",
            version="2.6",
        )
        temporary.replace(path)
    except (OSError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise BuildingSerializationError(
            f"cannot write building attribute Parquet {path}: {exc}"
        ) from exc


class BuildingSerializer:
    """Serialize only valid unjoined BuildingDataset modalities."""

    def serialize(
        self,
        dataset: BuildingDataset,
        validation: BuildingValidationResult,
        output_directory: str | Path,
        *,
        run_id: str,
    ) -> BuildingArtifactPaths:
        if not validation.valid:
            raise BuildingValidationError(
                "invalid BuildingDataset cannot be serialized"
            )
        directory = Path(output_directory)
        geometry_path = directory / "building_geometry.gpkg"
        attribute_path = directory / "building_attributes.parquet"
        metadata_path = directory / f"{run_id}_building_dataset.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _write_geometry(dataset, geometry_path)
            _write_attributes(dataset, attribute_path)
            geometry_hash = sha256_file(geometry_path)
            attribute_hash = sha256_file(attribute_path)
            payload = {
                "artifacts": {
                    "attribute_parquet": str(attribute_path),
                    "attribute_sha256": attribute_hash,
                    "geometry_geopackage": str(geometry_path),
                    "geometry_sha256": geometry_hash,
                },
                "building_dataset": dataset.metadata_dict(),
                "building_dataset_version": "1.0",
                "modalities_joined": False,
                "observed_area_created": False,
                "run_id": run_id,
                "stable_id_created": False,
                "validation": validation.to_dict(),
            }
            temporary = metadata_path.with_name(
                f".{metadata_path.name}.tmp"
            )
            temporary.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(metadata_path)
        except (OSError, TypeError, ValueError) as exc:
            raise BuildingSerializationError(
                f"cannot serialize BuildingDataset metadata: {exc}"
            ) from exc
        return BuildingArtifactPaths(
            geometry_geopackage=geometry_path,
            attribute_parquet=attribute_path,
            metadata_json=metadata_path,
            geometry_sha256=geometry_hash,
            attribute_sha256=attribute_hash,
        )
