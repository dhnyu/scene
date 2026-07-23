"""M1.6 split-count, uniqueness, hash, and provenance validation."""

from __future__ import annotations

from collections import Counter

from scene.core.config import DistrictAssignmentConfig
from scene.split.assign import assignment_content_hash
from scene.split.provenance import AssignmentValidation, DistrictAssignment


_PROVENANCE_FIELDS = (
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
)


def validate_assignment(
    assignment: DistrictAssignment,
    config: DistrictAssignmentConfig,
    *,
    regenerated_assignment: DistrictAssignment,
) -> AssignmentValidation:
    frame = assignment.frame
    counts = Counter(frame["split"].dropna().astype(str))
    duplicate_district = int(frame["district_id"].duplicated().sum())
    unassigned = int(
        frame["split"].isna().sum()
        + (~frame["split"].isin(("train", "validation", "test"))).sum()
    )
    duplicate_split_assignment = int(
        frame.duplicated(subset=["assignment_version", "district_id"]).sum()
    )
    missing = 0
    for field in _PROVENANCE_FIELDS:
        column = frame[field]
        missing += int(column.isna().sum())
        if column.dtype == object:
            missing += int((column.astype(str) == "").sum())
    rows = [
        (
            str(row.district_id),
            str(row.district_code),
            str(row.split),
        )
        for row in frame.itertuples()
    ]
    recomputed_hash = assignment_content_hash(rows)
    deterministic = (
        assignment.assignment_hash == regenerated_assignment.assignment_hash
        and assignment.search.assignment
        == regenerated_assignment.search.assignment
    )
    valid = (
        len(frame) == 25
        and counts["train"] == config.train_count
        and counts["validation"] == config.validation_count
        and counts["test"] == config.test_count
        and duplicate_district == 0
        and unassigned == 0
        and duplicate_split_assignment == 0
        and deterministic
        and assignment.assignment_hash == recomputed_hash
        and missing == 0
        and frame.crs is not None
        and frame.crs.to_epsg() == 5186
    )
    return AssignmentValidation(
        district_count=len(frame),
        train_count=counts["train"],
        validation_count=counts["validation"],
        test_count=counts["test"],
        duplicate_district_count=duplicate_district,
        unassigned_district_count=unassigned,
        duplicate_split_assignment_count=duplicate_split_assignment,
        deterministic_regeneration=deterministic,
        assignment_hash_deterministic=(
            assignment.assignment_hash == recomputed_hash
        ),
        provenance_complete=missing == 0,
        provenance_missing_count=missing,
        canonical_crs_valid=(
            frame.crs is not None and frame.crs.to_epsg() == 5186
        ),
        valid=valid,
    )
