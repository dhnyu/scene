from __future__ import annotations

from pathlib import Path

import pytest

from scene.inventory.models import InventoryRecord
from scene.inventory.registry import SourceDescriptor, SourceRegistry
from scene.inventory.scanner import scan_inventory
from scene.inventory.validator import validate_inventory_record


def _base_record(kind: str) -> InventoryRecord:
    return InventoryRecord(
        run_id="20260724_000000_KST",
        scanned_at_kst="2026-07-24T00:00:00+09:00",
        source_name="fixture",
        category="buildings",
        source_kind=kind,
        source_path="/read-only/fixture",
    )


def test_missing_source_validation_returns_errors() -> None:
    errors = validate_inventory_record(_base_record("tabular"))

    assert "path_missing" in errors
    assert "path_not_readable" in errors
    assert "sha256_missing_or_invalid" in errors


def test_vector_and_raster_metadata_gaps_are_reported() -> None:
    vector_errors = validate_inventory_record(_base_record("vector"))
    raster_errors = validate_inventory_record(_base_record("raster"))

    assert "vector_crs_missing" in vector_errors
    assert "vector_geometry_type_missing" in vector_errors
    assert "raster_crs_missing" in raster_errors
    assert "raster_resolution_missing" in raster_errors


def test_scan_continues_after_missing_source(tmp_path: Path) -> None:
    existing = tmp_path / "attributes.parquet"
    existing.write_bytes(b"fixture")
    registry = SourceRegistry(
        (
            SourceDescriptor(
                source_name="existing",
                category="buildings",
                kind="tabular",
                path=existing,
            ),
            SourceDescriptor(
                source_name="missing",
                category="dem",
                kind="raster",
                path=tmp_path / "missing.tif",
            ),
        )
    )

    scan = scan_inventory(
        registry,
        run_id="20260724_000000_KST",
        started_at_kst="2026-07-24T00:00:00+09:00",
    )

    assert scan.source_count == 2
    assert scan.records[0].valid
    assert not scan.records[1].valid
    assert scan.failure_count == 1


def test_hash_failure_is_captured_in_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    source.write_bytes(b"fixture")
    registry = SourceRegistry(
        (
            SourceDescriptor(
                source_name="source",
                category="buildings",
                kind="tabular",
                path=source,
            ),
        )
    )

    def fail_hash(path):
        raise OSError("fixture hash failure")

    monkeypatch.setattr("scene.inventory.scanner.sha256_file", fail_hash)
    scan = scan_inventory(
        registry,
        run_id="20260724_000000_KST",
        started_at_kst="2026-07-24T00:00:00+09:00",
    )

    assert not scan.records[0].valid
    assert any(
        error.startswith("sha256_error:OSError")
        for error in scan.records[0].validation_errors
    )
