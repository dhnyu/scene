#!/usr/bin/env python
"""Validate required audit products, JSON syntax, CSV schemas, and key content."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from audit_utils import audit_timestamp, load_configs, now_kst, read_json, write_json

EXPECTED_SCHEMAS = {
    "file_inventory": {
        "checked_at_kst", "source_path", "exists", "readable", "size_bytes", "sha256"
    },
    "layer_inventory": {
        "checked_at_kst", "source_path", "layer_name", "feature_count", "crs",
        "declared_geometry_type", "valid_geometry_count", "invalid_geometry_count"
    },
    "column_inventory": {
        "checked_at_kst", "source_path", "column_name", "data_type", "row_count",
        "null_count", "distinct_count"
    },
    "join_audit": {
        "checked_at_kst", "object_type", "geometry_key", "attribute_key",
        "geometry_match_rate", "attribute_match_rate", "cardinality", "classification"
    },
    "raster_inventory": {
        "checked_at_kst", "source_path", "crs", "width", "height", "resolution_x",
        "valid_cell_count", "nodata_cell_count"
    },
    "external_code_inventory": {
        "checked_at_kst", "repository", "source_path", "symbol", "line", "role", "verified"
    },
}


def latest_timestamp(reports: Path) -> str:
    candidates = sorted(reports.glob("*_audit_summary.json"))
    if not candidates:
        raise FileNotFoundError("No audit summary found")
    match = re.match(r"^(\d{8}_\d{6}_KST)_audit_summary\.json$", candidates[-1].name)
    if not match:
        raise ValueError(f"Unexpected summary filename: {candidates[-1]}")
    return match.group(1)


def validate_csv(path: Path, expected: set[str]) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or [])
        missing = sorted(expected - fields)
        count = sum(1 for _ in reader)
    return {"path": str(path), "rows": count, "missing_columns": missing,
            "valid": not missing and count > 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    root, _, _, _, _ = load_configs()
    reports = root / "reports"
    timestamp = latest_timestamp(reports) if args.latest else (
        args.timestamp or audit_timestamp()
    )
    required = [
        reports / f"{timestamp}_project_design.md",
        reports / f"{timestamp}_data_audit.md",
        reports / f"{timestamp}_external_code_audit.md",
        reports / f"{timestamp}_audit_summary.json",
        root / "metadata" / f"{timestamp}_project_structure.txt",
        root / "logs" / f"{timestamp}_project_audit.log",
    ] + [
        root / "metadata" / f"{timestamp}_{name}.csv"
        for name in EXPECTED_SCHEMAS
    ]
    missing_files = [str(path) for path in required if not path.is_file()]
    csv_results = []
    for name, schema in EXPECTED_SCHEMAS.items():
        path = root / "metadata" / f"{timestamp}_{name}.csv"
        if path.exists():
            csv_results.append(validate_csv(path, schema))
    json_results = []
    for path in [
        reports / f"{timestamp}_audit_summary.json",
        root / "metadata" / "raw" / timestamp / "data_audit.json",
        root / "metadata" / "raw" / timestamp / "external_code_audit.json",
        root / "metadata" / "raw" / timestamp / "python_environment.json",
    ]:
        try:
            value = read_json(path)
            json_results.append({"path": str(path), "valid": isinstance(value, dict)})
        except Exception as exc:
            json_results.append({
                "path": str(path), "valid": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    key_checks: dict[str, Any] = {}
    data_path = root / "metadata" / "raw" / timestamp / "data_audit.json"
    if data_path.exists():
        data = read_json(data_path)
        key_checks = {
            "source_unchanged": data.get("source_unchanged") is True,
            "has_actual_feature_counts": all(
                isinstance(row.get("feature_count"), int) and row["feature_count"] >= 0
                for row in data.get("layer_inventory", [])
            ),
            "all_vector_crs_present": all(
                bool(row.get("crs")) for row in data.get("layer_inventory", [])
            ),
            "has_geometry_types": all(
                bool(row.get("observed_geometry_types"))
                for row in data.get("layer_inventory", [])
            ),
            "recommended_joins_present": {
                row["object_type"] for row in data.get("join_audit", [])
                if row.get("recommended")
            } == {"building", "poi"},
            "raster_count_two": len(data.get("raster_inventory", [])) == 2,
        }
    summary_path = reports / f"{timestamp}_audit_summary.json"
    timestamp_consistent = True
    if summary_path.exists():
        summary = read_json(summary_path)
        timestamp_consistent = summary.get("timestamp") == timestamp and all(
            timestamp in str(path) for path in summary.get("reports", {}).values()
        )
    valid = (
        not missing_files
        and all(row["valid"] for row in csv_results)
        and all(row["valid"] for row in json_results)
        and all(key_checks.values())
        and timestamp_consistent
    )
    result = {
        "validated_at_kst": now_kst(),
        "timestamp": timestamp,
        "valid": valid,
        "missing_files": missing_files,
        "csv_results": csv_results,
        "json_results": json_results,
        "key_checks": key_checks,
        "timestamp_consistent": timestamp_consistent,
    }
    output = root / "metadata" / f"{timestamp}_validation.json"
    write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
