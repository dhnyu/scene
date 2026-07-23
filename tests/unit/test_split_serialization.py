from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from scene.split.assign import build_assignment
from scene.split.balancing import prepare_balance_model, search_assignment
from scene.split.exceptions import DistrictAssignmentError
from scene.split.serialization import write_assignment_artifacts
from scene.split.validator import validate_assignment
from test_split_assignment import make_split_fixture


def test_assignment_serialization_and_lock(tmp_path: Path) -> None:
    config, canonical, statistics = make_split_fixture(tmp_path)
    model = prepare_balance_model(statistics, canonical.districts, config)
    search = search_assignment(statistics, model, config)
    assignment = build_assignment(
        canonical,
        model,
        search,
        config,
        run_id="20260724_110000_KST",
    )
    validation = validate_assignment(
        assignment,
        config,
        regenerated_assignment=assignment,
    )

    artifacts = write_assignment_artifacts(
        assignment,
        validation,
        statistics,
        model,
        config,
        tmp_path / "outputs" / "split" / "run",
        tmp_path / "metadata",
    )

    assert pq.read_table(artifacts.assignment_parquet).num_rows == 25
    assert pq.read_table(artifacts.provenance_parquet).num_rows == 25
    assert (
        pq.ParquetFile(artifacts.assignment_parquet)
        .metadata.row_group(0)
        .column(0)
        .compression
        == "ZSTD"
    )
    payload = json.loads(artifacts.assignment_json.read_text(encoding="utf-8"))
    assert payload["assignment_hash"] == assignment.assignment_hash
    assert payload["validation"]["valid"] is True
    assert artifacts.assignment_lock_json.is_file()

    lock = json.loads(
        artifacts.assignment_lock_json.read_text(encoding="utf-8")
    )
    lock["assignment_hash"] = "f" * 64
    artifacts.assignment_lock_json.write_text(
        json.dumps(lock),
        encoding="utf-8",
    )
    rejected_output = tmp_path / "outputs" / "split" / "rejected"
    with pytest.raises(
        DistrictAssignmentError,
        match="immutable district assignment lock differs",
    ):
        write_assignment_artifacts(
            assignment,
            validation,
            statistics,
            model,
            config,
            rejected_output,
            tmp_path / "metadata",
        )
    assert not rejected_output.exists()
