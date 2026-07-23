from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from scene.inventory.models import InventoryRecord, InventoryScan
from scene.inventory.serialization import write_inventory


def test_json_and_zstandard_parquet_are_equivalent(tmp_path: Path) -> None:
    record = InventoryRecord(
        run_id="20260724_000000_KST",
        scanned_at_kst="2026-07-24T00:00:00+09:00",
        source_name="attributes",
        category="buildings",
        source_kind="tabular",
        source_path="/read-only/attributes.parquet",
        exists=True,
        readable=True,
        file_size=42,
        modified_time_kst="2026-07-23T10:00:00+09:00",
        sha256="a" * 64,
        valid=True,
        validation_errors=(),
        scan_duration_seconds=0.1,
    )
    scan = InventoryScan(
        run_id=record.run_id,
        started_at_kst=record.scanned_at_kst,
        completed_at_kst="2026-07-24T00:00:01+09:00",
        duration_seconds=1.0,
        records=(record,),
    )

    paths = write_inventory(scan, tmp_path / "inventory")

    json_record = json.loads(paths.json.read_text(encoding="utf-8"))["records"][0]
    table = pq.read_table(paths.parquet)
    parquet_record = table.to_pylist()[0]
    metadata = pq.ParquetFile(paths.parquet).metadata

    assert json_record == parquet_record
    assert table.num_rows == 1
    assert metadata.row_group(0).column(0).compression == "ZSTD"
