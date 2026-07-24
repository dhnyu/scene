"""Sequential CLI replay for the M1 release chain."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Sequence

import yaml

from scene.core.config import ProjectConfig
from scene.release_validation.exceptions import ReleaseValidationError
from scene.release_validation.models import ReleaseArtifacts, ReplayResult


_PERF_PATTERN = re.compile(r"__M1_9_PERF__:([0-9.]+):([0-9]+)")


def _parse_result(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "status" in value:
            return value
    raise ReleaseValidationError("CLI did not emit a machine-readable result")


def _run_cli(
    project_root: Path,
    arguments: Sequence[str],
    *,
    stage: str,
) -> tuple[dict[str, object], dict[str, object]]:
    command = [
        "/usr/bin/time",
        "-f",
        "__M1_9_PERF__:%e:%M",
        sys.executable,
        "-m",
        "scene.cli",
        *arguments,
    ]
    environment = dict(os.environ)
    source_root = str(project_root / "src")
    environment["PYTHONPATH"] = (
        source_root
        if not environment.get("PYTHONPATH")
        else f"{source_root}:{environment['PYTHONPATH']}"
    )
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    measured = time.perf_counter() - started
    match = _PERF_PATTERN.search(completed.stderr)
    performance = {
        "elapsed_seconds": measured,
        "maximum_resident_set_kib": (
            int(match.group(2)) if match is not None else None
        ),
        "stage": stage,
    }
    if completed.returncode != 0:
        raise ReleaseValidationError(
            f"CLI replay failed at {stage} with exit "
            f"{completed.returncode}: {completed.stderr[-4000:]}"
        )
    result = _parse_result(completed.stdout)
    if result.get("status") != "complete" or int(
        result.get("failure_count", 0)
    ):
        raise ReleaseValidationError(
            f"CLI replay did not complete cleanly at {stage}: {result}"
        )
    return result, performance


def _wait_for_distinct_run(previous_run_id: str) -> None:
    while time.strftime("%Y%m%d_%H%M%S_KST", time.localtime()) == previous_run_id:
        time.sleep(0.05)


def _write_replay_config(
    config: ProjectConfig,
    destination: Path,
    *,
    building: dict[str, object],
    roads: dict[str, object],
    pois: dict[str, object],
    raster: dict[str, object],
    ids: dict[str, object],
    boundary: dict[str, object],
    scenes: dict[str, object],
) -> Path:
    payload = config.to_dict()
    balancing = payload["district_assignment"]["balancing_sources"]
    balancing["building_geometry_path"] = building["geometry_geopackage"]
    balancing["road_geometry_path"] = roads["geometry_geopackage"]
    balancing["poi_geometry_path"] = pois["geometry_geopackage"]
    balancing["poi_attributes_path"] = pois["attribute_parquet"]
    miniature = payload["miniature_dataset"]
    miniature["scene_geometry_path"] = str(
        Path(str(scenes["output_directory"])) / "scene_footprints.gpkg"
    )
    miniature["scene_summary_path"] = str(
        Path(str(scenes["output_directory"])) / "scene_generation_summary.json"
    )
    miniature["stable_ids_path"] = ids["ids_parquet"]
    miniature["raster_metadata_path"] = raster["metadata_parquet"]
    boundary_run = str(boundary["run_id"])
    miniature["source_inventory_path"] = str(
        config.paths.output_root
        / "inventory"
        / boundary_run
        / f"{boundary_run}_source_inventory.json"
    )
    miniature["canonical_manifest_path"] = str(
        config.paths.output_root
        / "canonical"
        / boundary_run
        / f"{boundary_run}_canonical_manifest.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return destination


def replay_pipeline(
    config: ProjectConfig,
    config_path: Path,
    release_directory: Path,
) -> ReplayResult:
    """Replay every M1 CLI in order without overwriting a prior run."""

    root = config.paths.project_root
    stages: list[dict[str, object]] = []

    def execute(stage: str, arguments: Sequence[str]) -> dict[str, object]:
        result, performance = _run_cli(root, arguments, stage=stage)
        stages.append(
            {
                "command": [sys.executable, "-m", "scene.cli", *arguments],
                "performance": performance,
                "result": result,
                "stage": stage,
                "status": "PASS",
            }
        )
        return result

    common = ["--config", str(config_path)]
    inventory = execute("inventory", ["inventory", *common])
    canonical = execute(
        "canonical",
        [
            "canonical",
            *common,
            "--inventory",
            str(inventory["inventory_json"]),
        ],
    )
    building = execute(
        "building_adapter",
        [
            "buildings",
            *common,
            "--canonical-manifest",
            str(canonical["canonical_manifest_json"]),
        ],
    )
    roads = execute(
        "road_adapter",
        [
            "roads",
            *common,
            "--canonical-manifest",
            str(canonical["canonical_manifest_json"]),
        ],
    )
    pois = execute(
        "poi_adapter",
        [
            "pois",
            *common,
            "--canonical-manifest",
            str(canonical["canonical_manifest_json"]),
        ],
    )
    raster = execute("raster_adapter", ["raster", "build", *common])
    ids = execute(
        "stable_ids",
        [
            "ids",
            "build",
            *common,
            "--canonical-manifest",
            str(canonical["canonical_manifest_json"]),
        ],
    )
    _wait_for_distinct_run(str(canonical["run_id"]))
    boundary = execute(
        "district_boundary",
        ["boundary", "integrate-seoul-districts", *common],
    )
    split = execute("district_split", ["split", "assign", *common])
    scenes = execute(
        "scene_footprints",
        ["scenes", "generate-footprints", *common],
    )
    replay_config = _write_replay_config(
        config,
        release_directory / "replay_project.yaml",
        building=building,
        roads=roads,
        pois=pois,
        raster=raster,
        ids=ids,
        boundary=boundary,
        scenes=scenes,
    )
    miniature = execute(
        "miniature",
        ["miniature", "create", "--config", str(replay_config)],
    )
    boundary_run = str(boundary["run_id"])
    artifacts = ReleaseArtifacts(
        inventory_json=(
            config.paths.output_root
            / "inventory"
            / boundary_run
            / f"{boundary_run}_source_inventory.json"
        ),
        canonical_manifest=(
            config.paths.output_root
            / "canonical"
            / boundary_run
            / f"{boundary_run}_canonical_manifest.json"
        ),
        building_directory=Path(str(building["geometry_geopackage"])).parent,
        road_directory=Path(str(roads["geometry_geopackage"])).parent,
        poi_directory=Path(str(pois["geometry_geopackage"])).parent,
        raster_directory=Path(str(raster["metadata_parquet"])).parent,
        ids_directory=Path(str(ids["ids_parquet"])).parent,
        boundary_directory=Path(str(boundary["canonical_geopackage"])).parent,
        split_directory=Path(str(split["assignment_parquet"])).parent,
        scene_directory=Path(str(scenes["output_directory"])),
        miniature_directory=Path(str(miniature["output_directory"])),
    )
    return ReplayResult(
        artifacts=artifacts,
        stages=tuple(stages),
        replay_config=replay_config,
    )
