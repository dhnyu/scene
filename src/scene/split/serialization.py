"""Zstandard Parquet and JSON serialization for the frozen M1.6 split."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from shapely import to_wkb

from scene.core.config import DistrictAssignmentConfig
from scene.inventory.hashing import sha256_file
from scene.split.balancing import BalanceModel
from scene.split.exceptions import DistrictAssignmentError
from scene.split.provenance import (
    AssignmentValidation,
    BalancingStatistics,
    DistrictAssignment,
)


@dataclass(frozen=True, slots=True)
class AssignmentArtifacts:
    assignment_parquet: Path
    assignment_json: Path
    assignment_summary_json: Path
    balancing_statistics_json: Path
    provenance_parquet: Path
    assignment_lock_json: Path

    def to_dict(self) -> dict[str, str]:
        return {
            key: str(getattr(self, key))
            for key in self.__dataclass_fields__
        }


def _write_json(path: Path, value: object) -> None:
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


def aggregate_split_statistics(
    assignment: DistrictAssignment,
    model: BalanceModel,
    statistics: BalancingStatistics,
) -> dict[str, dict[str, Any]]:
    frame = model.frame.copy()
    frame["split"] = frame["district_code"].map(
        assignment.search.assignment
    )
    result: dict[str, dict[str, Any]] = {}
    for split, group in frame.groupby("split", sort=True):
        area = float(group["area_km2"].sum())
        landcover_counts = {
            code: int(
                sum(
                    counts.get(code, 0)
                    for counts in group["landcover_raw_code_counts"]
                )
            )
            for code in statistics.landcover_codes
        }
        landcover_total = sum(landcover_counts.values())
        category_counts = {
            category: int(
                sum(
                    counts.get(category, 0)
                    for counts in group["poi_category_1_counts"]
                )
            )
            for category in statistics.poi_categories
        }
        category_total = sum(category_counts.values())
        dem_weights = group["dem_valid_cell_count"].to_numpy(dtype=np.float64)
        dem_means = group["dem_mean_raw"].to_numpy(dtype=np.float64)
        dem_stds = group["dem_std_raw"].to_numpy(dtype=np.float64)
        dem_mean = float(np.sum(dem_weights * dem_means) / dem_weights.sum())
        dem_second = float(
            np.sum(dem_weights * (dem_stds**2 + dem_means**2))
            / dem_weights.sum()
        )
        result[str(split)] = {
            "district_count": len(group),
            "district_codes": sorted(group["district_code"].astype(str)),
            "area_km2": area,
            "eligible_scene_estimate": int(
                group["eligible_scene_estimate"].sum()
            ),
            "building_count": int(group["building_count"].sum()),
            "building_density_per_km2": float(
                group["building_count"].sum() / area
            ),
            "road_length_km": float(group["road_length_km"].sum()),
            "road_density_km_per_km2": float(
                group["road_length_km"].sum() / area
            ),
            "poi_count": int(group["poi_count"].sum()),
            "poi_density_per_km2": float(group["poi_count"].sum() / area),
            "poi_category_1_counts": category_counts,
            "poi_category_1_proportions": {
                key: value / category_total
                for key, value in category_counts.items()
            },
            "landcover_raw_code_counts": landcover_counts,
            "landcover_raw_code_proportions": {
                key: value / landcover_total
                for key, value in landcover_counts.items()
            },
            "dem_valid_cell_count": int(dem_weights.sum()),
            "dem_mean_raw": dem_mean,
            "dem_std_raw": float(
                np.sqrt(max(0.0, dem_second - dem_mean**2))
            ),
            "context_cluster_count": int(
                group["context_cluster_id"].nunique()
            ),
            "spatial_cluster_count": int(
                group["spatial_cluster_id"].nunique()
            ),
            "radial_band_count": int(group["radial_band_id"].nunique()),
            "connected_component_count": (
                assignment.search.connected_component_count_by_split[str(split)]
            ),
        }
    return result


def _assignment_table(assignment: DistrictAssignment) -> pa.Table:
    frame = assignment.frame
    return pa.Table.from_pydict(
        {
            "district_id": frame["district_id"].astype(str).to_list(),
            "district_code": frame["district_code"].astype(str).to_list(),
            "district_name": frame["district_name"].astype(str).to_list(),
            "split": frame["split"].astype(str).to_list(),
            "assignment_seed": frame["assignment_seed"].astype("int64").to_list(),
            "assignment_version": frame["assignment_version"].astype(str).to_list(),
            "assignment_hash": frame["assignment_hash"].astype(str).to_list(),
            "assignment_config_hash": frame[
                "assignment_config_hash"
            ].astype(str).to_list(),
            "balance_statistics_hash": frame[
                "balance_statistics_hash"
            ].astype(str).to_list(),
            "run_id": frame["run_id"].astype(str).to_list(),
            "context_cluster_id": frame[
                "context_cluster_id"
            ].astype("int16").to_list(),
            "spatial_cluster_id": frame[
                "spatial_cluster_id"
            ].astype("int16").to_list(),
            "radial_band_id": frame["radial_band_id"].astype("int8").to_list(),
            "district_geometry_wkb": [
                to_wkb(geometry, byte_order=1, output_dimension=2)
                for geometry in frame.geometry
            ],
        }
    )


def write_assignment_artifacts(
    assignment: DistrictAssignment,
    validation: AssignmentValidation,
    statistics: BalancingStatistics,
    model: BalanceModel,
    config: DistrictAssignmentConfig,
    output_directory: Path,
    metadata_directory: Path,
) -> AssignmentArtifacts:
    """Serialize one run and create or verify the permanent assignment lock."""

    rows = [
        {
            key: getattr(row, key)
            for key in (
                "district_id",
                "district_code",
                "district_name",
                "split",
                "assignment_seed",
                "assignment_version",
                "assignment_hash",
                "assignment_config_hash",
                "balance_statistics_hash",
                "run_id",
                "context_cluster_id",
                "spatial_cluster_id",
                "radial_band_id",
            )
        }
        for row in assignment.frame.itertuples()
    ]
    lock_directory = metadata_directory / "split"
    lock_path = (
        lock_directory
        / f"{config.assignment_version}_assignment_lock.json"
    )
    lock_payload = {
        "assignment": [
            {
                "district_code": row["district_code"],
                "district_id": row["district_id"],
                "split": row["split"],
            }
            for row in rows
        ],
        "assignment_config_hash": assignment.assignment_config_hash,
        "assignment_hash": assignment.assignment_hash,
        "assignment_seed": config.assignment_seed,
        "assignment_version": config.assignment_version,
        "balance_statistics_hash": assignment.balance_statistics_hash,
        "canonical_boundary_content_hash": (
            assignment.canonical_input.content_hash
        ),
    }
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if existing != lock_payload:
            raise DistrictAssignmentError(
                "immutable district assignment lock differs from regeneration"
            )

    output_directory.mkdir(parents=True, exist_ok=False)
    assignment_parquet = output_directory / "district_assignment.parquet"
    provenance_parquet = output_directory / "provenance.parquet"
    assignment_json = output_directory / "district_assignment.json"
    summary_json = output_directory / "assignment_summary.json"
    statistics_json = output_directory / "balancing_statistics.json"
    table = _assignment_table(assignment)
    pq.write_table(table, assignment_parquet, compression="zstd")
    provenance = pa.Table.from_pydict(
        {
            "district_id": assignment.frame["district_id"].astype(str).to_list(),
            "district_code": assignment.frame[
                "district_code"
            ].astype(str).to_list(),
            "canonical_boundary_path": [
                str(assignment.canonical_input.geopackage_path)
            ] * 25,
            "canonical_boundary_layer": [
                assignment.canonical_input.layer
            ] * 25,
            "canonical_boundary_sha256": [
                assignment.canonical_input.geopackage_sha256
            ] * 25,
            "canonical_boundary_content_hash": [
                assignment.canonical_input.content_hash
            ] * 25,
            "assignment_seed": [config.assignment_seed] * 25,
            "assignment_version": [config.assignment_version] * 25,
            "assignment_hash": [assignment.assignment_hash] * 25,
            "assignment_config_hash": [
                assignment.assignment_config_hash
            ] * 25,
            "balance_statistics_hash": [
                assignment.balance_statistics_hash
            ] * 25,
            "run_id": assignment.frame["run_id"].astype(str).to_list(),
        }
    )
    pq.write_table(provenance, provenance_parquet, compression="zstd")
    _write_json(
        assignment_json,
        {
            "assignment_hash": assignment.assignment_hash,
            "assignment_version": config.assignment_version,
            "districts": rows,
            "validation": validation.to_dict(),
        },
    )
    aggregate = aggregate_split_statistics(assignment, model, statistics)
    _write_json(
        summary_json,
        {
            "assignment_hash": assignment.assignment_hash,
            "assignment_config_hash": assignment.assignment_config_hash,
            "assignment_version": config.assignment_version,
            "balance_statistics_hash": assignment.balance_statistics_hash,
            "search": asdict(assignment.search),
            "split_statistics": aggregate,
            "validation": validation.to_dict(),
        },
    )
    enriched_records = json.loads(
        model.frame.to_json(
            orient="records",
            force_ascii=False,
            double_precision=15,
        )
    )
    _write_json(
        statistics_json,
        {
            "context_definition": model.context_definition,
            "districts": enriched_records,
            "landcover_codes": list(statistics.landcover_codes),
            "method": statistics.method,
            "poi_categories": list(statistics.poi_categories),
            "raw_statistics_hash": statistics.statistics_hash,
            "source_provenance": statistics.source_provenance,
            "statistics_hash": model.statistics_hash,
        },
    )

    if not lock_path.exists():
        lock_directory.mkdir(parents=True, exist_ok=True)
        _write_json(lock_path, lock_payload)

    return AssignmentArtifacts(
        assignment_parquet=assignment_parquet,
        assignment_json=assignment_json,
        assignment_summary_json=summary_json,
        balancing_statistics_json=statistics_json,
        provenance_parquet=provenance_parquet,
        assignment_lock_json=lock_path,
    )


def artifact_hashes(artifacts: AssignmentArtifacts) -> dict[str, str]:
    return {
        key: sha256_file(Path(value))
        for key, value in artifacts.to_dict().items()
    }
