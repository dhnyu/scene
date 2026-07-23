"""Shared, read-only audit helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def project_root() -> Path:
    return Path(os.environ.get("SCENE_PROJECT_ROOT", "~/scene")).expanduser().resolve()


def audit_timestamp() -> str:
    value = os.environ.get("AUDIT_TIMESTAMP")
    if not value:
        value = datetime.now(KST).strftime("%Y%m%d_%H%M%S_KST")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def load_configs() -> tuple[Path, str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = project_root()
    timestamp = audit_timestamp()
    paths = load_yaml(root / "config" / "paths.yaml")
    data = load_yaml(root / "config" / "data.yaml")
    audit = load_yaml(root / "config" / "audit.yaml")
    return root, timestamp, paths, data, audit


def raw_dir(root: Path, timestamp: str) -> Path:
    path = root / "metadata" / "raw" / timestamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def run_command(command: list[str], timeout: int = 120) -> dict[str, Any]:
    started = datetime.now(KST)
    try:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "elapsed_seconds": (datetime.now(KST) - started).total_seconds(),
        }
    except Exception as exc:
        return {
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": (datetime.now(KST) - started).total_seconds(),
        }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=json_default)
        stream.write("\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: serialize_csv_value(row.get(key)) for key in fieldnames
            })


def serialize_csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=json_default)
    if hasattr(value, "item"):
        return value.item()
    return value


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def quick_fingerprint(path: Path, sample_bytes: int = 1024 * 1024) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as stream:
        digest.update(stream.read(sample_bytes))
        if size > sample_bytes:
            stream.seek(max(0, size - sample_bytes))
            digest.update(stream.read(sample_bytes))
    return digest.hexdigest()


def source_state(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        stat = path.stat()
        rows.append({
            "path": str(path.resolve()),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    return rows
