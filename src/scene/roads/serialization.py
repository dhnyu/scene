"""D-011A serialization for validated road datasets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from scene.inventory.hashing import sha256_file
from scene.roads.dataset import RoadLinkDataset, RoadNodeDataset
from scene.roads.exceptions import RoadSerializationError, RoadValidationError
from scene.roads.validator import RoadValidationResult


@dataclass(frozen=True, slots=True)
class RoadArtifactPaths:
    """Materialized road artifacts and hashes."""

    geometry_geopackage: Path
    link_attribute_parquet: Path
    node_attribute_parquet: Path
    metadata_json: Path
    geometry_sha256: str
    link_attribute_sha256: str
    node_attribute_sha256: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {key: str(item) for key, item in value.items()}


def _layer_metadata(
    dataset: RoadLinkDataset | RoadNodeDataset,
) -> dict[str, str]:
    provenance = dataset.geometry.provenance_metadata
    source = dataset.geometry.source_metadata
    return {
        "canonical_frame_sha256": provenance.canonical_frame_sha256,
        "canonical_run_id": provenance.canonical_run_id,
        "canonical_schema_sha256": provenance.canonical_schema_sha256,
        "source_file_sha256": source.source_file_sha256,
        "source_name": source.source_name,
    }


def _write_geometry(
    links: RoadLinkDataset,
    nodes: RoadNodeDataset,
    path: Path,
) -> None:
    temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
    temporary.unlink(missing_ok=True)
    link_table = links.geometry_dataframe.select(
        ["source_link_id", "source_fid", "geometry_wkb"]
    )
    node_table = nodes.geometry_dataframe.select(
        ["source_node_id", "source_fid", "geometry_wkb"]
    )
    try:
        pyogrio.write_arrow(
            link_table,
            temporary,
            layer="road_links",
            driver="GPKG",
            geometry_name="geometry_wkb",
            geometry_type=links.geometry.geometry_type,
            crs=links.crs,
            layer_metadata=_layer_metadata(links),
        )
        pyogrio.write_arrow(
            node_table,
            temporary,
            layer="road_nodes",
            driver="GPKG",
            geometry_name="geometry_wkb",
            geometry_type=nodes.geometry.geometry_type,
            crs=nodes.crs,
            layer_metadata=_layer_metadata(nodes),
            append=True,
        )
        temporary.replace(path)
    except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise RoadSerializationError(
            f"cannot write road GeoPackage {path}: {exc}"
        ) from exc


def _write_attributes(table: pa.Table, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            version="2.6",
        )
        temporary.replace(path)
    except (OSError, pa.ArrowException) as exc:
        temporary.unlink(missing_ok=True)
        raise RoadSerializationError(
            f"cannot write road attribute Parquet {path}: {exc}"
        ) from exc


class RoadSerializer:
    """Serialize only valid, unjoined road datasets."""

    def serialize(
        self,
        links: RoadLinkDataset,
        nodes: RoadNodeDataset,
        validation: RoadValidationResult,
        output_directory: str | Path,
        *,
        run_id: str,
    ) -> RoadArtifactPaths:
        if not validation.valid:
            raise RoadValidationError(
                "invalid road datasets cannot be serialized"
            )
        directory = Path(output_directory)
        geometry_path = directory / "road_geometry.gpkg"
        link_path = directory / "road_link_attributes.parquet"
        node_path = directory / "road_node_attributes.parquet"
        metadata_path = directory / f"{run_id}_road_datasets.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _write_geometry(links, nodes, geometry_path)
            _write_attributes(links.attribute_dataframe, link_path)
            _write_attributes(nodes.attribute_dataframe, node_path)
            geometry_hash = sha256_file(geometry_path)
            link_hash = sha256_file(link_path)
            node_hash = sha256_file(node_path)
            payload = {
                "artifacts": {
                    "geometry_geopackage": str(geometry_path),
                    "geometry_sha256": geometry_hash,
                    "link_attribute_parquet": str(link_path),
                    "link_attribute_sha256": link_hash,
                    "node_attribute_parquet": str(node_path),
                    "node_attribute_sha256": node_hash,
                },
                "canonical_field_availability": {
                    "road_class": {
                        "canonical_column": "road_type",
                        "status": "available",
                    },
                    "road_name": {
                        "canonical_column": "source_road_name",
                        "status": "available",
                    },
                    "road_rank": {
                        "canonical_column": "road_rank",
                        "status": "available",
                    },
                    "bridge": {
                        "canonical_column": None,
                        "status": "not_declared_in_canonical_schema",
                    },
                    "tunnel": {
                        "canonical_column": None,
                        "status": "not_declared_in_canonical_schema",
                    },
                    "direction": {
                        "canonical_column": None,
                        "status": "not_declared_in_canonical_schema",
                    },
                },
                "geometry_attributes_joined": False,
                "link_node_connected": False,
                "road_datasets_version": "1.0",
                "road_link_dataset": links.metadata_dict(),
                "road_node_dataset": nodes.metadata_dict(),
                "run_id": run_id,
                "stable_id_created": False,
                "topology_created": False,
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
            raise RoadSerializationError(
                f"cannot serialize road dataset metadata: {exc}"
            ) from exc
        return RoadArtifactPaths(
            geometry_geopackage=geometry_path,
            link_attribute_parquet=link_path,
            node_attribute_parquet=node_path,
            metadata_json=metadata_path,
            geometry_sha256=geometry_hash,
            link_attribute_sha256=link_hash,
            node_attribute_sha256=node_hash,
        )
