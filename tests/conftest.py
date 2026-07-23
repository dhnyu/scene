from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml


def make_config_data(root: Path) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "project_name": "scene-test",
        "timezone": "Asia/Seoul",
        "paths": {
            "project_root": str(root),
            "input_root": str(root / "inputs"),
            "external_root": str(root / "external"),
            "output_root": str(root / "outputs"),
            "reports_dir": str(root / "reports"),
            "logs_dir": str(root / "logs"),
            "metadata_dir": str(root / "metadata"),
            "resolved_config_dir": str(root / "resolved"),
            "tmp_dir": str(root / "tmp"),
        },
        "storage": {
            "geometry_format": "geopackage",
            "tabular_format": "parquet",
            "parquet_compression": "zstd",
            "resolved_config_format": "yaml",
            "run_summary_format": "json",
            "miniature_raster_format": "geotiff",
            "source_raster_policy": "read_only_reference",
            "geopackage_usage": "inspection_and_archive",
            "per_scene_pt_files": "forbidden",
            "training_cache_format": "open",
        },
    }


def write_config(path: Path, data: Mapping[str, object]) -> Path:
    path.write_text(
        yaml.safe_dump(dict(data), sort_keys=False),
        encoding="utf-8",
    )
    return path
