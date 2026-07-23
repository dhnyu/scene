"""D-011A serialization for a validated POIDataset."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.inventory.hashing import sha256_file
from scene.pois.category import (
    CATEGORY_COLUMNS,
    CATEGORY_NORMALIZATION,
    CATEGORY_PATH_COLUMN,
    CATEGORY_PATH_NULL_TOKEN,
    CATEGORY_PATH_SEPARATOR,
)
from scene.pois.dataset import POIDataset
from scene.pois.exceptions import POISerializationError, POIValidationError
from scene.pois.validator import POIValidationResult


@dataclass(frozen=True, slots=True)
class POIArtifactPaths:
    """Materialized POIDataset artifacts and hashes."""

    geometry_geopackage: Path
    attribute_parquet: Path
    metadata_json: Path
    geometry_sha256: str
    attribute_sha256: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {key: str(item) for key, item in value.items()}


def _write_geometry(dataset: POIDataset, path: Path) -> None:
    temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
    temporary.unlink(missing_ok=True)
    table = dataset.geometry_dataframe.select(
        ["source_poi_id", "source_fid", "geometry_wkb"]
    )
    source = dataset.geometry.source_metadata
    provenance = dataset.geometry.provenance_metadata
    try:
        pyogrio.write_arrow(
            table,
            temporary,
            layer="pois",
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
        raise POISerializationError(
            f"cannot write POI GeoPackage {path}: {exc}"
        ) from exc


def _write_attributes(dataset: POIDataset, path: Path) -> None:
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
        raise POISerializationError(
            f"cannot write POI attribute Parquet {path}: {exc}"
        ) from exc


class POISerializer:
    """Serialize only a valid, unjoined POIDataset."""

    def serialize(
        self,
        dataset: POIDataset,
        validation: POIValidationResult,
        output_directory: str | Path,
        *,
        run_id: str,
    ) -> POIArtifactPaths:
        if not validation.valid:
            raise POIValidationError(
                "invalid POIDataset cannot be serialized"
            )
        directory = Path(output_directory)
        geometry_path = directory / "poi_geometry.gpkg"
        attribute_path = directory / "poi_attributes.parquet"
        metadata_path = directory / f"{run_id}_poi_dataset.json"
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
                "category_path_contract": {
                    "column": CATEGORY_PATH_COLUMN,
                    "normalization": CATEGORY_NORMALIZATION,
                    "separator": CATEGORY_PATH_SEPARATOR,
                    "source_columns": list(CATEGORY_COLUMNS),
                    "source_null_token": CATEGORY_PATH_NULL_TOKEN,
                    "source_labels_preserved": True,
                },
                "geometry_attributes_joined": False,
                "geometry_modality_created": False,
                "poi_dataset": dataset.metadata_dict(),
                "poi_dataset_version": "1.0",
                "records_deduplicated": False,
                "records_merged": False,
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
            raise POISerializationError(
                f"cannot serialize POIDataset metadata: {exc}"
            ) from exc
        return POIArtifactPaths(
            geometry_geopackage=geometry_path,
            attribute_parquet=attribute_path,
            metadata_json=metadata_path,
            geometry_sha256=geometry_hash,
            attribute_sha256=attribute_hash,
        )
