#!/usr/bin/env python
"""Record operating system, hardware, Python, CUDA, and geospatial libraries."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from audit_utils import load_configs, now_kst, raw_dir, run_command, setup_logging, write_json

LOGGER = setup_logging("python_environment")

PACKAGES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "geopandas": "geopandas",
    "shapely": "shapely",
    "rasterio": "rasterio",
    "fiona": "fiona",
    "pyogrio": "pyogrio",
    "torch": "torch",
    "torch-geometric": "torch_geometric",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "PyYAML": "yaml",
    "faiss": "faiss",
}


def package_status(distribution: str, module: str) -> dict[str, Any]:
    try:
        imported = importlib.import_module(module)
    except Exception as exc:
        return {
            "installed": False,
            "version": "미설치",
            "import_error": f"{type(exc).__name__}: {exc}",
        }
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = getattr(imported, "__version__", "unknown")
    return {"installed": True, "version": str(version), "import_error": None}


def filesystem_info(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def torch_status() -> dict[str, Any]:
    try:
        import torch

        return {
            "version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "gpu_count": torch.cuda.device_count(),
            "gpu_names": [
                torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
            ],
            "compiled_cuda": torch.version.cuda,
            "cudnn_version": torch.backends.cudnn.version(),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    root, timestamp, paths, _, _ = load_configs()
    output = raw_dir(root, timestamp) / "python_environment.json"
    commands = {
        "os_release": run_command(["cat", "/etc/os-release"]),
        "uname": run_command(["uname", "-a"]),
        "lscpu": run_command(["lscpu"]),
        "memory": run_command(["free", "-b"]),
        "df": run_command([
            "df", "-BT", paths["project_root"], paths["input_root"], paths["external_root"]
        ]),
        "nvidia_smi_summary": run_command([
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ]),
        "nvidia_smi": run_command(["nvidia-smi"]),
        "nvcc": run_command(["nvcc", "--version"]),
        "gdal": run_command(["gdalinfo", "--version"]),
        "proj": run_command(["proj"]),
        "geos": run_command(["geos-config", "--version"]),
        "conda_envs": run_command(["conda", "info", "--envs"]),
    }
    result = {
        "checked_at_kst": now_kst(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV", "미확정"),
            "conda_prefix": os.environ.get("CONDA_PREFIX", "미확정"),
        },
        "packages": {
            name: package_status(name, module) for name, module in PACKAGES.items()
        },
        "torch": torch_status(),
        "filesystems": {
            key: filesystem_info(Path(value))
            for key, value in {
                "project_root": paths["project_root"],
                "input_root": paths["input_root"],
                "external_root": paths["external_root"],
            }.items()
        },
        "commands": commands,
    }
    write_json(output, result)
    LOGGER.info("Python environment audit written: %s", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
