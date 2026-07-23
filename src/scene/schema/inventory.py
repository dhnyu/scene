"""M1.2 inventory discovery and validation for M1.3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scene.core.config import ProjectConfig
from scene.schema.exceptions import SourceMappingError


def find_latest_inventory(metadata_dir: str | Path) -> Path:
    """Return the latest timestamped M1.2 JSON inventory."""

    directory = Path(metadata_dir) / "inventory"
    candidates = sorted(directory.glob("*_source_inventory.json"))
    if not candidates:
        raise SourceMappingError(
            f"no M1.2 source inventory JSON found in {directory}"
        )
    return candidates[-1]


def load_inventory_records(
    path: str | Path,
    config: ProjectConfig,
) -> dict[str, Mapping[str, Any]]:
    """Load valid M1.2 records and require exact registry/path agreement."""

    inventory_path = Path(path).expanduser().resolve(strict=False)
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceMappingError(
            f"cannot read source inventory {inventory_path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("records"), list
    ):
        raise SourceMappingError("source inventory has no records list")

    records: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(payload["records"]):
        if not isinstance(raw, Mapping):
            raise SourceMappingError(
                f"source inventory records[{index}] is not a mapping"
            )
        name = raw.get("source_name")
        if not isinstance(name, str) or not name:
            raise SourceMappingError(
                f"source inventory records[{index}] has no source_name"
            )
        if name in records:
            raise SourceMappingError(f"duplicate inventory source_name: {name}")
        records[name] = raw

    configured_names = {source.source_name for source in config.sources}
    if set(records) != configured_names:
        raise SourceMappingError(
            "inventory registry differs from configuration: "
            f"missing={sorted(configured_names - set(records))}, "
            f"extra={sorted(set(records) - configured_names)}"
        )
    for source in config.sources:
        record = records[source.source_name]
        if record.get("valid") is not True:
            raise SourceMappingError(
                f"inventory source is not valid: {source.source_name}"
            )
        if Path(str(record.get("source_path"))).resolve(strict=False) != source.path:
            raise SourceMappingError(
                f"inventory path differs for source: {source.source_name}"
            )
        sha256 = record.get("sha256")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise SourceMappingError(
                f"inventory SHA-256 is invalid: {source.source_name}"
            )
    return records
