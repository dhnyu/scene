"""Atomic Zstandard serialization for stable IDs and provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scene.id.exceptions import StableIdSerializationError
from scene.id.generator import ID_CONTRACT_VERSION
from scene.id.provenance import StableIdDataset
from scene.id.validator import StableIdValidation
from scene.inventory.hashing import sha256_file


@dataclass(frozen=True, slots=True)
class StableIdArtifacts:
    ids_parquet: Path
    provenance_parquet: Path
    ids_json: Path
    ids_parquet_sha256: str
    provenance_parquet_sha256: str
    ids_json_sha256: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {key: str(item) for key, item in value.items()}


def _write_parquet(table: pa.Table, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            version="2.6",
            row_group_size=131_072,
        )
        temporary.replace(path)
    except (OSError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise StableIdSerializationError(
            f"cannot write stable ID Parquet {path}: {exc}"
        ) from exc


class StableIdSerializer:
    """Write the three contracted M1.5 artifacts."""

    def serialize(
        self,
        dataset: StableIdDataset,
        validation: StableIdValidation,
        output_dir: str | Path,
        *,
        run_id: str,
        config_hash: str,
    ) -> StableIdArtifacts:
        directory = Path(output_dir)
        ids_path = directory / "ids.parquet"
        provenance_path = directory / "provenance.parquet"
        json_path = directory / "ids.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StableIdSerializationError(
                f"cannot create stable ID output directory {directory}: {exc}"
            ) from exc
        _write_parquet(dataset.ids, ids_path)
        _write_parquet(dataset.provenance, provenance_path)
        ids_hash = sha256_file(ids_path)
        provenance_hash = sha256_file(provenance_path)
        payload = {
            "artifacts": {
                "ids_parquet": str(ids_path),
                "ids_parquet_sha256": ids_hash,
                "provenance_parquet": str(provenance_path),
                "provenance_parquet_sha256": provenance_hash,
            },
            "canonical_input": dataset.source.to_dict(),
            "config_hash": config_hash,
            "counts": validation.counts,
            "generation_digest": dataset.generation_digest,
            "id_contract_version": ID_CONTRACT_VERSION,
            "materialized_id_types": [
                "building_id",
                "road_link_id",
                "road_node_id",
                "poi_id",
                "source_object_id",
            ],
            "run_id": run_id,
            "scene_based_ids_materialized": False,
            "validation": validation.to_dict(),
        }
        temporary = json_path.with_name(f".{json_path.name}.tmp")
        try:
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
            temporary.replace(json_path)
        except (OSError, TypeError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            raise StableIdSerializationError(
                f"cannot write stable ID JSON {json_path}: {exc}"
            ) from exc
        return StableIdArtifacts(
            ids_parquet=ids_path,
            provenance_parquet=provenance_path,
            ids_json=json_path,
            ids_parquet_sha256=ids_hash,
            provenance_parquet_sha256=provenance_hash,
            ids_json_sha256=sha256_file(json_path),
        )
