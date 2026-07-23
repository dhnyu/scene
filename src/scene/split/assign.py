"""Build immutable assignment rows and deterministic content hashes."""

from __future__ import annotations

import json

import geopandas as gpd

from scene.core.config import DistrictAssignmentConfig
from scene.id.generator import canonical_hash
from scene.split.balancing import BalanceModel
from scene.split.provenance import (
    AssignmentSearch,
    CanonicalDistrictInput,
    DistrictAssignment,
)


def assignment_config_hash(config: DistrictAssignmentConfig) -> str:
    """Hash only approved split inputs and settings, not unrelated project config."""

    payload = json.dumps(
        config.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return canonical_hash("district_assignment_config", payload)


def assignment_content_hash(
    rows: list[tuple[str, str, str]],
) -> str:
    """Hash only the sorted district-to-split content."""

    fields = [
        value
        for row in sorted(rows)
        for value in row
    ]
    return canonical_hash(
        "district_assignment",
        *fields,
    )


def build_assignment(
    canonical: CanonicalDistrictInput,
    model: BalanceModel,
    search: AssignmentSearch,
    config: DistrictAssignmentConfig,
    *,
    run_id: str,
) -> DistrictAssignment:
    """Attach one split to each canonical district without changing geometry."""

    config_hash = assignment_config_hash(config)
    district_rows = [
        (
            str(row.district_id),
            str(row.district_code),
            search.assignment[str(row.district_code)],
        )
        for row in canonical.districts.itertuples()
    ]
    content_hash = assignment_content_hash(district_rows)
    diagnostics = model.frame[
        [
            "district_code",
            "context_cluster_id",
            "spatial_cluster_id",
            "radial_band_id",
        ]
    ]
    frame = canonical.districts[
        ["district_id", "district_code", "district_name", "geometry"]
    ].merge(diagnostics, on="district_code", validate="one_to_one")
    frame["split"] = frame["district_code"].map(search.assignment)
    frame["assignment_seed"] = config.assignment_seed
    frame["assignment_version"] = config.assignment_version
    frame["assignment_hash"] = content_hash
    frame["assignment_config_hash"] = config_hash
    frame["balance_statistics_hash"] = model.statistics_hash
    frame["run_id"] = run_id
    assignment_frame = gpd.GeoDataFrame(
        frame,
        geometry="geometry",
        crs=canonical.districts.crs,
    ).sort_values("district_code", ignore_index=True)
    return DistrictAssignment(
        frame=assignment_frame,
        assignment_hash=content_hash,
        assignment_config_hash=config_hash,
        balance_statistics_hash=model.statistics_hash,
        search=search,
        canonical_input=canonical,
    )
