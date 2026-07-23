"""Explicit structured logging configuration."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from scene.core.exceptions import PathValidationError


_KST = ZoneInfo("Asia/Seoul")


class JsonFormatter(logging.Formatter):
    """Serialize one log record as a stable JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp_kst": datetime.fromtimestamp(
                record.created,
                tz=_KST,
            ).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = run_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self._run_id
        return True


def configure_logging(
    log_path: str | Path,
    run_id: str,
    *,
    level: str = "INFO",
) -> logging.Logger:
    """Configure the project logger without changing the root logger."""

    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        raise ValueError(f"unsupported log level: {level}")

    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        raise PathValidationError(f"cannot open log file {path}: {exc}") from exc

    formatter = JsonFormatter()
    run_filter = _RunIdFilter(run_id)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(run_filter)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(run_filter)

    logger = logging.getLogger("scene")
    logger.handlers.clear()
    logger.setLevel(numeric_level)
    logger.propagate = False
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger
