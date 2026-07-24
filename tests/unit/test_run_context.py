from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from conftest import make_config_data, write_config
from scene.core.config import load_config
from scene.core.exceptions import RunContextError
from scene.core.run_context import (
    UNAVAILABLE_GIT_COMMIT,
    collect_run_metadata,
    format_kst_run_id,
    get_git_commit,
)


def test_kst_run_id_is_deterministic() -> None:
    instant = datetime(2026, 7, 23, 14, 37, 26, tzinfo=timezone.utc)

    assert format_kst_run_id(instant) == "20260723_233726_KST"
    assert format_kst_run_id(instant) == format_kst_run_id(instant)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(RunContextError, match="timezone-aware"):
        format_kst_run_id(datetime(2026, 7, 23, 23, 37, 26))


def test_git_commit_is_safe_outside_repository(tmp_path: Path) -> None:
    assert get_git_commit(tmp_path) == UNAVAILABLE_GIT_COMMIT


def test_git_commit_is_safe_before_first_commit(tmp_path: Path) -> None:
    result = subprocess.run(
        ["git", "init", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert get_git_commit(tmp_path) == UNAVAILABLE_GIT_COMMIT


def test_run_metadata_contains_required_provenance(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path / "project.yaml", make_config_data(tmp_path))
    )
    instant = datetime(2026, 7, 23, 14, 37, 26, tzinfo=timezone.utc)

    metadata = collect_run_metadata(
        config,
        started_at=instant,
        git_repository=tmp_path,
    )

    assert metadata.run_id == "20260723_233726_KST"
    assert metadata.git_commit == UNAVAILABLE_GIT_COMMIT
    assert metadata.resolved_config_hash == config.canonical_hash
    assert metadata.python_version
    assert metadata.platform
    assert metadata.shapely_version
    assert metadata.geos_version
