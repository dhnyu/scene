from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from conftest import make_config_data, write_config
from scene.core.config import load_config
from scene.core.logging import configure_logging
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import collect_run_metadata


def _metadata(tmp_path: Path):
    config = load_config(
        write_config(tmp_path / "project.yaml", make_config_data(tmp_path))
    )
    return collect_run_metadata(
        config,
        started_at=datetime(2026, 7, 23, 14, 37, 26, tzinfo=timezone.utc),
        git_repository=tmp_path,
    )


def test_markdown_and_json_reports_are_created(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)

    paths = write_reports(
        tmp_path / "reports",
        "20260723_233726_KST_test",
        title="Foundation Test",
        metadata=metadata,
        summary={"status": "complete", "tests_passed": 1},
        sections=(ReportSection("Verification", "All checks passed."),),
    )

    payload = json.loads(paths.json.read_text(encoding="utf-8"))
    assert paths.markdown.is_file()
    assert payload["metadata"]["run_id"] == "20260723_233726_KST"
    assert payload["summary"]["tests_passed"] == 1
    assert "## Verification" in paths.markdown.read_text(encoding="utf-8")


def test_structured_logging_writes_json(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "run.jsonl"
    logger = configure_logging(log_path, "20260723_233726_KST")

    logger.info("foundation test")
    for handler in logger.handlers:
        handler.flush()

    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["run_id"] == "20260723_233726_KST"
    assert record["message"] == "foundation test"
    logger.handlers.clear()
    logging.getLogger("scene").propagate = True
