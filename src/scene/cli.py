"""Command-line entry point for milestone workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scene.buildings.workflow import run_buildings
from scene.boundaries.workflow import run_seoul_district_integration
from scene.core.config import load_config, write_resolved_config
from scene.core.exceptions import SceneError
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import collect_run_metadata
from scene.id.workflow import run_stable_ids
from scene.inventory.workflow import run_inventory
from scene.m4.workflow import run_m4_stage
from scene.miniature.workflow import run_miniature
from scene.observations.workflow import run_observation_contract
from scene.observations.buildings import run_building_observations
from scene.observations.roads import run_road_observations
from scene.pois.workflow import run_pois
from scene.raster.workflow import run_raster
from scene.release_validation.workflow import run_release_validation
from scene.roads.workflow import run_roads
from scene.schema.workflow import run_canonical
from scene.scenes.workflow import run_scene_footprints
from scene.split.workflow import run_district_assignment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scene",
        description="Spatial scene research implementation workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    foundation = subparsers.add_parser(
        "foundation",
        help="Validate configuration and record an M1.1 foundation run.",
    )
    foundation.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    foundation.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    inventory = subparsers.add_parser(
        "inventory",
        help="Run the read-only M1.2 source inventory.",
    )
    inventory.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    inventory.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    canonical = subparsers.add_parser(
        "canonical",
        help="Run M1.3 canonical schema validation and source mapping.",
    )
    canonical.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    canonical.add_argument(
        "--inventory",
        type=Path,
        help="M1.2 inventory JSON; defaults to the latest registered inventory.",
    )
    canonical.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    buildings = subparsers.add_parser(
        "buildings",
        help="Run the M1.4.1 Building Adapter.",
    )
    buildings.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    buildings.add_argument(
        "--canonical-manifest",
        type=Path,
        help="M1.3 canonical manifest; defaults to the latest valid run.",
    )
    buildings.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    roads = subparsers.add_parser(
        "roads",
        help="Run the M1.4.2 Road Adapter.",
    )
    roads.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    roads.add_argument(
        "--canonical-manifest",
        type=Path,
        help="M1.3 canonical manifest; defaults to the latest valid run.",
    )
    roads.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    pois = subparsers.add_parser(
        "pois",
        help="Run the M1.4.3 POI Adapter.",
    )
    pois.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    pois.add_argument(
        "--canonical-manifest",
        type=Path,
        help="M1.3 canonical manifest; defaults to the latest valid run.",
    )
    pois.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    raster = subparsers.add_parser(
        "raster",
        help="Run read-only raster adapter workflows.",
    )
    raster_subparsers = raster.add_subparsers(
        dest="raster_command",
        required=True,
    )
    raster_build = raster_subparsers.add_parser(
        "build",
        help="Build M1.4.4 Landcover and DEM metadata references.",
    )
    raster_build.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    raster_build.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    ids = subparsers.add_parser(
        "ids",
        help="Run stable ID workflows.",
    )
    ids_subparsers = ids.add_subparsers(
        dest="ids_command",
        required=True,
    )
    ids_build = ids_subparsers.add_parser(
        "build",
        help="Build the M1.5 stable ID and provenance registry.",
    )
    ids_build.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    ids_build.add_argument(
        "--canonical-manifest",
        type=Path,
        help="M1.3 canonical manifest; defaults to the latest valid run.",
    )
    ids_build.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    boundary = subparsers.add_parser(
        "boundary",
        help="Run administrative-boundary integration workflows.",
    )
    boundary_subparsers = boundary.add_subparsers(
        dest="boundary_command",
        required=True,
    )
    integrate_districts = boundary_subparsers.add_parser(
        "integrate-seoul-districts",
        help="Run M1.5.1 boundary, inventory, and schema backfills.",
    )
    integrate_districts.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    integrate_districts.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    split = subparsers.add_parser(
        "split",
        help="Run permanent spatial split workflows.",
    )
    split_subparsers = split.add_subparsers(
        dest="split_command",
        required=True,
    )
    split_assign = split_subparsers.add_parser(
        "assign",
        help="Run the fixed M1.6 Seoul district assignment.",
    )
    split_assign.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    split_assign.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    scenes = subparsers.add_parser(
        "scenes",
        help="Run deterministic scene-footprint workflows.",
    )
    scenes_subparsers = scenes.add_subparsers(
        dest="scenes_command",
        required=True,
    )
    generate_footprints = scenes_subparsers.add_parser(
        "generate-footprints",
        help="Generate the M1.7 fixed 500 m scene footprints.",
        description="Generate the M1.7 fixed 500 m scene footprints.",
    )
    generate_footprints.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    generate_footprints.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    miniature = subparsers.add_parser(
        "miniature",
        help="Run candidate-only miniature dataset workflows.",
    )
    miniature_subparsers = miniature.add_subparsers(
        dest="miniature_command",
        required=True,
    )
    miniature_create = miniature_subparsers.add_parser(
        "create",
        help="Create the M1.8 candidate-only integration fixture.",
    )
    miniature_create.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    miniature_create.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    release = subparsers.add_parser(
        "release",
        help="Run release-candidate validation workflows.",
    )
    release_subparsers = release.add_subparsers(
        dest="release_command",
        required=True,
    )
    release_validate = release_subparsers.add_parser(
        "validate",
        help="Run the M1.9 end-to-end release validation.",
    )
    release_validate.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    release_validate.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )

    m4 = subparsers.add_parser(
        "m4",
        help="Run explicit M4 representation-learning skeleton stages.",
    )
    m4_subparsers = m4.add_subparsers(
        dest="m4_command",
        required=True,
    )
    m4_run_stage = m4_subparsers.add_parser(
        "run-stage",
        help="Run one explicit M4 stage. M4.4+ cannot be auto-started.",
    )
    m4_run_stage.add_argument(
        "--stage",
        choices=("M4.1", "M4.2", "M4.3", "M4.3A", "M4.4", "M4.5", "M4.6", "M4.7", "M4.8", "M4.9"),
        required=True,
        help="Explicit M4 stage to run. M4.4+ cannot be auto-started.",
    )
    m4_run_stage.add_argument(
        "--config",
        type=Path,
        default=Path("configs/m4/m4_skeleton.yaml"),
        help="Path to the M4 skeleton YAML configuration.",
    )
    m4_run_stage.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for M4.1 skeleton stage metadata.",
    )
    m4_run_stage.add_argument(
        "--m4-1-dir",
        type=Path,
        help="M4.1 stage directory containing M4_1_PASS; required for M4.2.",
    )
    m4_run_stage.add_argument(
        "--m4-2-dir",
        type=Path,
        help="M4.2 stage directory containing M4_2_PASS; required for M4.3.",
    )
    m4_run_stage.add_argument(
        "--m4-3-dir",
        type=Path,
        help="M4.3 stage directory containing M4_3_PASS; required for M4.3A.",
    )
    m4_run_stage.add_argument(
        "--m4-3a-dir",
        type=Path,
        help="M4.3A stage directory containing M4_3A_PASS; required for M4.4.",
    )
    m4_run_stage.add_argument(
        "--m4-4-dir",
        type=Path,
        help="M4.4 stage directory containing M4_4_PASS; required for M4.5.",
    )
    m4_run_stage.add_argument(
        "--m4-5-dir",
        type=Path,
        help="M4.5 stage directory containing M4_5_PASS; required for M4.6.",
    )
    m4_run_stage.add_argument(
        "--m4-6-dir",
        type=Path,
        help="M4.6 stage directory containing M4_6_PASS; required for M4.7.",
    )
    m4_run_stage.add_argument(
        "--m4-7-dir",
        type=Path,
        help="M4.7 stage directory containing M4_7_PASS; required for M4.8.",
    )
    m4_run_stage.add_argument(
        "--m4-8-dir",
        type=Path,
        help="M4.8 stage directory containing M4_8_PASS; required for M4.9.",
    )
    m4_run_stage.add_argument(
        "--workers",
        type=int,
        default=40,
        help="M4 stage worker count. M4.3A requires 40 workers.",
    )

    observations = subparsers.add_parser(
        "observations",
        help="Run scene-observation contract workflows.",
    )
    observation_subparsers = observations.add_subparsers(
        dest="observation_command",
        required=True,
    )
    validate_observation_contract = observation_subparsers.add_parser(
        "validate-contract",
        help="Validate the M2.1 contract and synthetic fixture.",
    )
    validate_observation_contract.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    validate_observation_contract.add_argument(
        "--schema",
        type=Path,
        required=True,
        help="Path to scene_observation_schema.yaml.",
    )
    validate_observation_contract.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Path to the M2.1 synthetic fixture YAML.",
    )
    validate_observation_contract.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    build_building_observations = observation_subparsers.add_parser(
        "build-buildings",
        help="Run noncanonical experimental Building Observations.",
    )
    build_building_observations.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    build_building_observations.add_argument(
        "--scene-geometry",
        type=Path,
        help="Scene footprint GeoPackage; defaults to latest complete scene artifact.",
    )
    build_building_observations.add_argument(
        "--building-geometry",
        type=Path,
        help="Building geometry GeoPackage; defaults to latest complete building artifact.",
    )
    build_building_observations.add_argument(
        "--building-attributes",
        type=Path,
        help="Building attributes Parquet; defaults to latest complete building artifact.",
    )
    build_building_observations.add_argument(
        "--stable-ids",
        type=Path,
        help="Stable IDs Parquet; defaults to latest complete ID artifact.",
    )
    build_building_observations.add_argument(
        "--stable-id-provenance",
        type=Path,
        help="Stable ID provenance Parquet; defaults to latest complete ID artifact.",
    )
    build_building_observations.add_argument(
        "--schema",
        type=Path,
        help="Observation schema; defaults to docs/contracts/scene_observation_schema.yaml.",
    )
    build_building_observations.add_argument(
        "--experimental-noncanonical",
        action="store_true",
        help=(
            "Required guard: this Python producer is preserved for audit and "
            "M3 comparison only. It is not an official M2 production command."
        ),
    )
    build_building_observations.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    build_road_observations = observation_subparsers.add_parser(
        "build-roads",
        help="Run noncanonical experimental Road Observations.",
    )
    build_road_observations.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the project YAML configuration.",
    )
    build_road_observations.add_argument(
        "--scene-geometry",
        type=Path,
        help="Scene footprint GeoPackage; defaults to latest complete scene artifact.",
    )
    build_road_observations.add_argument(
        "--road-geometry",
        type=Path,
        help="Road geometry GeoPackage; defaults to latest complete road artifact.",
    )
    build_road_observations.add_argument(
        "--road-link-attributes",
        type=Path,
        help="Road link attributes Parquet; defaults to latest complete road artifact.",
    )
    build_road_observations.add_argument(
        "--road-node-attributes",
        type=Path,
        help="Road node attributes Parquet; defaults to latest complete road artifact.",
    )
    build_road_observations.add_argument(
        "--stable-ids",
        type=Path,
        help="Stable IDs Parquet; defaults to latest complete ID artifact.",
    )
    build_road_observations.add_argument(
        "--stable-id-provenance",
        type=Path,
        help="Stable ID provenance Parquet; defaults to latest complete ID artifact.",
    )
    build_road_observations.add_argument(
        "--schema",
        type=Path,
        help="Observation schema; defaults to docs/contracts/scene_observation_schema.yaml.",
    )
    build_road_observations.add_argument(
        "--experimental-noncanonical",
        action="store_true",
        help=(
            "Required guard: this Python producer is preserved for audit and "
            "M3 comparison only. It is not an official M2 production command."
        ),
    )
    build_road_observations.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def run_foundation(config_path: Path, log_level: str) -> dict[str, object]:
    config = load_config(config_path)
    validate_paths(config.paths)
    create_output_directories(config.paths)
    metadata = collect_run_metadata(config)

    log_path = config.paths.logs_dir / f"{metadata.run_id}_m1_1_foundation.jsonl"
    logger = configure_logging(log_path, metadata.run_id, level=log_level)
    logger.info("M1.1 foundation run started")

    resolved_path = (
        config.paths.resolved_config_dir
        / f"{metadata.run_id}_resolved_config.yaml"
    )
    write_resolved_config(config, resolved_path)

    basename = f"{metadata.run_id}_m1_1_foundation_run"
    reports = write_reports(
        config.paths.reports_dir,
        basename,
        title="M1.1 Project Foundation Run",
        metadata=metadata,
        summary={
            "resolved_config": str(resolved_path),
            "status": "complete",
        },
        sections=(
            ReportSection(
                "Scope",
                "Configuration and project paths were validated. No GIS source "
                "was read and no dataset was materialized.",
            ),
        ),
    )
    logger.info("M1.1 foundation run completed")
    return {
        "json_report": str(reports.json),
        "markdown_report": str(reports.markdown),
        "resolved_config": str(resolved_path),
        "run_id": metadata.run_id,
        "status": "complete",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "foundation":
            result = run_foundation(args.config, args.log_level)
        elif args.command == "inventory":
            result = run_inventory(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "canonical":
            result = run_canonical(
                args.config,
                inventory_path=args.inventory,
                log_level=args.log_level,
            )
        elif args.command == "buildings":
            result = run_buildings(
                args.config,
                canonical_manifest=args.canonical_manifest,
                log_level=args.log_level,
            )
        elif args.command == "roads":
            result = run_roads(
                args.config,
                canonical_manifest=args.canonical_manifest,
                log_level=args.log_level,
            )
        elif args.command == "pois":
            result = run_pois(
                args.config,
                canonical_manifest=args.canonical_manifest,
                log_level=args.log_level,
            )
        elif args.command == "raster":
            result = run_raster(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "boundary":
            result = run_seoul_district_integration(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "split":
            result = run_district_assignment(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "scenes":
            result = run_scene_footprints(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "miniature":
            result = run_miniature(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "release":
            result = run_release_validation(
                args.config,
                log_level=args.log_level,
            )
        elif args.command == "m4":
            result = run_m4_stage(
                args.config,
                stage_id=args.stage,
                output_dir=args.output_dir,
                m4_1_dir=args.m4_1_dir,
                m4_2_dir=args.m4_2_dir,
                m4_3_dir=args.m4_3_dir,
                m4_3a_dir=args.m4_3a_dir,
                m4_4_dir=args.m4_4_dir,
                m4_5_dir=args.m4_5_dir,
                m4_6_dir=args.m4_6_dir,
                m4_7_dir=args.m4_7_dir,
                m4_8_dir=args.m4_8_dir,
                workers=args.workers,
            )
        elif args.command == "observations":
            if args.observation_command == "validate-contract":
                result = run_observation_contract(
                    args.config,
                    schema_path=args.schema,
                    fixture_path=args.fixture,
                    log_level=args.log_level,
                )
            elif args.observation_command == "build-buildings":
                if not args.experimental_noncanonical:
                    parser.exit(
                        2,
                        "scene: error: build-buildings is noncanonical and "
                        "experimental; pass --experimental-noncanonical only "
                        "for audit or M3 comparison runs.\n",
                    )
                result = run_building_observations(
                    args.config,
                    scene_geometry=args.scene_geometry,
                    building_geometry=args.building_geometry,
                    building_attributes=args.building_attributes,
                    stable_ids=args.stable_ids,
                    stable_id_provenance=args.stable_id_provenance,
                    schema_path=args.schema,
                    log_level=args.log_level,
                )
            else:
                if not args.experimental_noncanonical:
                    parser.exit(
                        2,
                        "scene: error: build-roads is noncanonical and "
                        "experimental; pass --experimental-noncanonical only "
                        "for audit or M3 comparison runs.\n",
                    )
                result = run_road_observations(
                    args.config,
                    scene_geometry=args.scene_geometry,
                    road_geometry=args.road_geometry,
                    road_link_attributes=args.road_link_attributes,
                    road_node_attributes=args.road_node_attributes,
                    stable_ids=args.stable_ids,
                    stable_id_provenance=args.stable_id_provenance,
                    schema_path=args.schema,
                    log_level=args.log_level,
                )
        else:
            result = run_stable_ids(
                args.config,
                canonical_manifest=args.canonical_manifest,
                log_level=args.log_level,
            )
    except SceneError as exc:
        parser.exit(2, f"scene: error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
