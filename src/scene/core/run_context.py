"""Deterministic KST run identifiers and environment provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import platform
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

from scene.core.config import ProjectConfig
from scene.core.exceptions import RunContextError


KST = ZoneInfo("Asia/Seoul")
RUN_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_KST$")
UNAVAILABLE_GIT_COMMIT = "unavailable"


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """Minimum reproducibility metadata for every execution."""

    run_id: str
    started_at_kst: str
    git_commit: str
    python_version: str
    platform: str
    resolved_config_hash: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def format_kst_run_id(value: datetime | None = None) -> str:
    """Return a deterministic run ID for an aware timestamp."""

    instant = value or datetime.now(tz=KST)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise RunContextError("run timestamp must be timezone-aware")
    return instant.astimezone(KST).strftime("%Y%m%d_%H%M%S_KST")


def get_git_commit(repository: str | Path) -> str:
    """Return HEAD or a stable sentinel outside a repository or before commit."""

    path = Path(repository)
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return UNAVAILABLE_GIT_COMMIT

    commit = result.stdout.strip().lower()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", commit) is None:
        return UNAVAILABLE_GIT_COMMIT
    return commit


def collect_run_metadata(
    config: ProjectConfig,
    *,
    started_at: datetime | None = None,
    git_repository: str | Path | None = None,
) -> RunMetadata:
    """Collect run metadata without mutating the filesystem or logging state."""

    instant = started_at or datetime.now(tz=KST)
    run_id = format_kst_run_id(instant)
    started_at_kst = instant.astimezone(KST).isoformat(timespec="seconds")
    repository = git_repository or config.paths.project_root
    return RunMetadata(
        run_id=run_id,
        started_at_kst=started_at_kst,
        git_commit=get_git_commit(repository),
        python_version=platform.python_version(),
        platform=platform.platform(),
        resolved_config_hash=config.canonical_hash,
    )
