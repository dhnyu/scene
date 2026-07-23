"""Command-line entry point for milestone workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scene.core.config import load_config, write_resolved_config
from scene.core.exceptions import SceneError
from scene.core.logging import configure_logging
from scene.core.paths import create_output_directories, validate_paths
from scene.core.reporting import ReportSection, write_reports
from scene.core.run_context import collect_run_metadata
from scene.inventory.workflow import run_inventory
from scene.schema.workflow import run_canonical


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
        else:
            result = run_canonical(
                args.config,
                inventory_path=args.inventory,
                log_level=args.log_level,
            )
    except SceneError as exc:
        parser.exit(2, f"scene: error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
