"""Independent content, geometry, ID, manifest and repository audits."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path
import pkgutil
import re
import tokenize
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import pyogrio
from shapely import is_empty, is_valid, to_wkb
import yaml

from scene.core.config import ProjectConfig
from scene.inventory.hashing import sha256_file
from scene.release_validation.exceptions import ReleaseValidationError
from scene.release_validation.models import ReleaseArtifacts
from scene.schema.schema import load_canonical_schema


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"JSON artifact is not an object: {path}")
    return value


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def inventory_content(path: Path) -> dict[str, object]:
    payload = _json(path)
    volatile = {
        "config_hash",
        "run_id",
        "scan_duration_seconds",
        "scanned_at_kst",
    }
    records = [
        {
            key: value
            for key, value in record.items()
            if key not in volatile
        }
        for record in payload["records"]
    ]
    records.sort(key=lambda row: str(row["source_name"]))
    return {
        "content_hash": _canonical_digest(records),
        "row_count": len(records),
        "source_names": [str(row["source_name"]) for row in records],
    }


def canonical_content(path: Path) -> dict[str, object]:
    payload = _json(path)
    frames = [
        {
            "frame_name": frame["frame_name"],
            "output_sha256": frame["output_sha256"],
            "row_count": int(frame["row_count"]),
            "source_name": frame["source_name"],
            "valid": bool(frame["valid"]),
        }
        for frame in payload["frames"]
    ]
    frames.sort(key=lambda row: str(row["source_name"]))
    return {
        "content_hash": _canonical_digest(frames),
        "frame_count": len(frames),
        "frames": frames,
        "schema_sha256": payload["schema_sha256"],
        "schema_version": payload["schema_version"],
    }


def id_content(directory: Path) -> dict[str, object]:
    metadata = _json(directory / "ids.json")
    table = pq.read_table(
        directory / "ids.parquet",
        columns=["canonical_object_id", "entity_type"],
    ).to_pandas()
    pairs = sorted(
        zip(
            table["entity_type"].astype(str),
            table["canonical_object_id"].astype(str),
            strict=True,
        )
    )
    return {
        "content_hash": _canonical_digest(pairs),
        "generation_digest": metadata["generation_digest"],
        "row_count": len(table),
    }


def boundary_content(directory: Path) -> dict[str, object]:
    metadata = _json(directory / "seoul_district_metadata.json")
    return {
        "content_hash": metadata["canonical_content_hash"],
        "row_count": int(metadata["feature_count"]),
    }


def split_content(directory: Path) -> dict[str, object]:
    summary = _json(directory / "assignment_summary.json")
    return {
        "assignment_hash": summary["assignment_hash"],
        "row_count": pq.read_metadata(
            directory / "district_assignment.parquet"
        ).num_rows,
    }


def scene_content(directory: Path) -> dict[str, object]:
    summary = _json(directory / "scene_generation_summary.json")
    return {
        "content_hash": summary["scene_content_hash"],
        "row_count": pq.read_metadata(
            directory / "scene_footprints.parquet"
        ).num_rows,
    }


def miniature_content(directory: Path) -> dict[str, object]:
    summary = _json(directory / "summary.json")
    return {
        "candidate_counts": summary["candidate_counts"],
        "content_hash": summary["content_hash"],
        "row_count": int(summary["scene_count"]),
    }


def hash_comparison(
    reference: ReleaseArtifacts,
    replay: ReleaseArtifacts,
) -> dict[str, object]:
    values = {
        "inventory": (
            inventory_content(reference.inventory_json),
            inventory_content(replay.inventory_json),
        ),
        "canonical": (
            canonical_content(reference.canonical_manifest),
            canonical_content(replay.canonical_manifest),
        ),
        "ids": (
            id_content(reference.ids_directory),
            id_content(replay.ids_directory),
        ),
        "boundary": (
            boundary_content(reference.boundary_directory),
            boundary_content(replay.boundary_directory),
        ),
        "split": (
            split_content(reference.split_directory),
            split_content(replay.split_directory),
        ),
        "scene": (
            scene_content(reference.scene_directory),
            scene_content(replay.scene_directory),
        ),
        "miniature": (
            miniature_content(reference.miniature_directory),
            miniature_content(replay.miniature_directory),
        ),
    }
    stages: dict[str, object] = {}
    for stage, (left, right) in values.items():
        if stage == "canonical":
            content_match = (
                left["content_hash"] == right["content_hash"]
                and left["frame_count"] == right["frame_count"]
                and left["frames"] == right["frames"]
                and left["schema_version"] == right["schema_version"]
            )
        else:
            content_match = left == right
        stages[stage] = {
            "match": content_match,
            "provenance_match": left == right,
            "reference": left,
            "replay": right,
        }
    return {
        "all_match": all(bool(value["match"]) for value in stages.values()),
        "all_provenance_match": all(
            bool(value["provenance_match"]) for value in stages.values()
        ),
        "stages": stages,
    }


def _geometry_layer_audit(
    path: Path,
    layer: str,
    id_column: str,
    expected_types: set[str],
) -> dict[str, object]:
    frame = pyogrio.read_dataframe(
        path,
        layer=layer,
        columns=[id_column],
    )
    if id_column not in frame:
        raise ReleaseValidationError(f"{layer} lacks ID column {id_column}")
    geometry = frame.geometry
    ids = frame[id_column].astype("string")
    null_count = int(geometry.isna().sum())
    empty_count = int(is_empty(geometry.array).sum())
    invalid_count = int((~is_valid(geometry.array)).sum())
    unexpected_types = int(
        (~geometry.geom_type.isin(expected_types) & geometry.notna()).sum()
    )
    wkb_hashes = [
        hashlib.sha256(
            to_wkb(item, byte_order=1, output_dimension=2)
        ).hexdigest()
        for item in geometry
        if item is not None
    ]
    keyed = sorted(
        zip(ids.astype(str), wkb_hashes, strict=True)
    )
    crs = frame.crs.to_string() if frame.crs is not None else None
    result = {
        "crs": crs,
        "duplicate_geometry_row_count": len(wkb_hashes)
        - len(set(wkb_hashes)),
        "duplicate_id_count": int(ids.duplicated().sum()),
        "empty_geometry_count": empty_count,
        "fingerprint": _canonical_digest(keyed),
        "invalid_geometry_count": invalid_count,
        "null_geometry_count": null_count,
        "null_id_count": int(ids.isna().sum()),
        "row_count": len(frame),
        "unexpected_geometry_type_count": unexpected_types,
    }
    result["valid"] = (
        crs == "EPSG:5186"
        and not any(
            result[key]
            for key in (
                "duplicate_id_count",
                "empty_geometry_count",
                "invalid_geometry_count",
                "null_geometry_count",
                "null_id_count",
                "unexpected_geometry_type_count",
            )
        )
    )
    return result


def _geometry_specs(artifacts: ReleaseArtifacts) -> tuple[tuple[str, Path, str, str, set[str]], ...]:
    return (
        (
            "building",
            artifacts.building_directory / "building_geometry.gpkg",
            "buildings",
            "source_building_id",
            {"Polygon", "MultiPolygon"},
        ),
        (
            "road_link",
            artifacts.road_directory / "road_geometry.gpkg",
            "road_links",
            "source_link_id",
            {"LineString", "MultiLineString"},
        ),
        (
            "road_node",
            artifacts.road_directory / "road_geometry.gpkg",
            "road_nodes",
            "source_node_id",
            {"Point"},
        ),
        (
            "poi",
            artifacts.poi_directory / "poi_geometry.gpkg",
            "pois",
            "source_poi_id",
            {"Point"},
        ),
        (
            "district",
            artifacts.boundary_directory / "seoul_administrative_boundaries.gpkg",
            "seoul_sigungu",
            "district_id",
            {"Polygon", "MultiPolygon"},
        ),
        (
            "scene",
            artifacts.scene_directory / "scene_footprints.gpkg",
            "scene_footprints",
            "scene_footprint_id",
            {"Polygon"},
        ),
    )


def geometry_audit(
    reference: ReleaseArtifacts,
    replay: ReleaseArtifacts,
) -> dict[str, object]:
    results: dict[str, object] = {}
    ref_specs = _geometry_specs(reference)
    replay_specs = _geometry_specs(replay)
    for ref, rep in zip(ref_specs, replay_specs, strict=True):
        name, path, layer, id_column, expected = ref
        replay_name, replay_path, replay_layer, replay_id, replay_expected = rep
        if (name, layer, id_column, expected) != (
            replay_name,
            replay_layer,
            replay_id,
            replay_expected,
        ):
            raise ReleaseValidationError("geometry specification mismatch")
        left = _geometry_layer_audit(
            path,
            layer,
            id_column,
            expected,
        )
        right = _geometry_layer_audit(
            replay_path,
            replay_layer,
            replay_id,
            replay_expected,
        )
        results[name] = {
            "content_match": (
                left["fingerprint"] == right["fingerprint"]
                and left["row_count"] == right["row_count"]
            ),
            "reference": left,
            "replay": right,
        }
    return {
        "all_content_match": all(
            bool(value["content_match"]) for value in results.values()
        ),
        "all_valid": all(
            bool(value["reference"]["valid"])
            and bool(value["replay"]["valid"])
            for value in results.values()
        ),
        "duplicate_geometry_interpretation": (
            "Exact geometry equality is reported separately from duplicate "
            "object IDs; distinct stable source IDs may share coordinates."
        ),
        "layers": results,
    }


def _read_column(path: Path, column: str) -> pd.Series:
    return pq.read_table(path, columns=[column]).to_pandas()[column].astype(
        "string"
    )


def _pair_correspondence(
    geometry_path: Path,
    layer: str,
    geometry_id: str,
    attribute_path: Path,
    attribute_id: str,
) -> dict[str, object]:
    geometry = pyogrio.read_dataframe(
        geometry_path,
        layer=layer,
        columns=[geometry_id],
        read_geometry=False,
    )[geometry_id].astype("string")
    attributes = _read_column(attribute_path, attribute_id)
    return {
        "attribute_only_count": len(set(attributes) - set(geometry)),
        "attribute_row_count": len(attributes),
        "geometry_only_count": len(set(geometry) - set(attributes)),
        "geometry_row_count": len(geometry),
        "id_set_match": set(geometry) == set(attributes),
    }


def id_audit(artifacts: ReleaseArtifacts) -> dict[str, object]:
    ids = pq.read_table(
        artifacts.ids_directory / "ids.parquet",
        columns=[
            "canonical_object_id",
            "entity_type",
            "source_native_id",
        ],
    ).to_pandas()
    entity_counts = {
        str(key): int(value)
        for key, value in ids.groupby("entity_type", observed=True).size().items()
    }
    correspondence = {
        "building": _pair_correspondence(
            artifacts.building_directory / "building_geometry.gpkg",
            "buildings",
            "source_building_id",
            artifacts.building_directory / "building_attributes.parquet",
            "source_building_id",
        ),
        "road_link": _pair_correspondence(
            artifacts.road_directory / "road_geometry.gpkg",
            "road_links",
            "source_link_id",
            artifacts.road_directory / "road_link_attributes.parquet",
            "source_link_id",
        ),
        "road_node": _pair_correspondence(
            artifacts.road_directory / "road_geometry.gpkg",
            "road_nodes",
            "source_node_id",
            artifacts.road_directory / "road_node_attributes.parquet",
            "source_node_id",
        ),
        "poi": _pair_correspondence(
            artifacts.poi_directory / "poi_geometry.gpkg",
            "pois",
            "source_poi_id",
            artifacts.poi_directory / "poi_attributes.parquet",
            "source_poi_id",
        ),
    }
    district_ids = pyogrio.read_dataframe(
        artifacts.boundary_directory / "seoul_administrative_boundaries.gpkg",
        layer="seoul_sigungu",
        columns=["district_id"],
        read_geometry=False,
    )["district_id"].astype("string")
    scene_ids = _read_column(
        artifacts.scene_directory / "scene_footprints.parquet",
        "scene_footprint_id",
    )
    known = {
        entity: set(
            ids.loc[
                ids["entity_type"].astype(str) == entity,
                "canonical_object_id",
            ].astype(str)
        )
        for entity in ("building", "road_link", "road_node", "poi")
    }
    candidate_files = {
        "building": ("scene_building_candidates.parquet", "building_id"),
        "road_link": ("scene_road_link_candidates.parquet", "road_link_id"),
        "road_node": ("scene_road_node_candidates.parquet", "road_node_id"),
        "poi": ("scene_poi_candidates.parquet", "poi_id"),
    }
    unknown_candidates = {
        entity: len(
            set(
                _read_column(
                    artifacts.miniature_directory / filename,
                    column,
                )
            )
            - known[entity]
        )
        for entity, (filename, column) in candidate_files.items()
    }
    result = {
        "canonical_collision_count": int(
            ids["canonical_object_id"].duplicated().sum()
        ),
        "correspondence": correspondence,
        "district_duplicate_count": int(district_ids.duplicated().sum()),
        "district_null_count": int(district_ids.isna().sum()),
        "entity_counts": entity_counts,
        "null_stable_id_count": int(ids["canonical_object_id"].isna().sum()),
        "scene_duplicate_count": int(scene_ids.duplicated().sum()),
        "scene_null_count": int(scene_ids.isna().sum()),
        "unknown_candidate_counts": unknown_candidates,
    }
    result["valid"] = (
        not any(
            result[key]
            for key in (
                "canonical_collision_count",
                "district_duplicate_count",
                "district_null_count",
                "null_stable_id_count",
                "scene_duplicate_count",
                "scene_null_count",
            )
        )
        and all(value["id_set_match"] for value in correspondence.values())
        and not any(unknown_candidates.values())
    )
    return result


def manifest_audit(
    config: ProjectConfig,
    artifacts: ReleaseArtifacts,
) -> dict[str, object]:
    inventory = _json(artifacts.inventory_json)
    source_rows = {
        row["source_name"]: row for row in inventory["records"]
    }
    source_hash_mismatch = {
        source.source_name: (
            source_rows.get(source.source_name, {}).get("sha256"),
            sha256_file(source.path),
        )
        for source in config.sources
        if source.source_name not in source_rows
        or source_rows[source.source_name]["sha256"] != sha256_file(source.path)
    }
    canonical = _json(artifacts.canonical_manifest)
    frame_hash_mismatch: dict[str, object] = {}
    for frame in canonical["frames"]:
        path = Path(frame["output_parquet"])
        actual = sha256_file(path) if path.is_file() else None
        if actual != frame["output_sha256"]:
            frame_hash_mismatch[str(frame["source_name"])] = {
                "actual": actual,
                "expected": frame["output_sha256"],
                "path": str(path),
            }
    required = [
        artifacts.inventory_json,
        artifacts.canonical_manifest,
        artifacts.ids_directory / "ids.json",
        artifacts.boundary_directory / "seoul_district_metadata.json",
        artifacts.split_directory / "assignment_summary.json",
        artifacts.scene_directory / "scene_generation_summary.json",
        artifacts.miniature_directory / "summary.json",
    ]
    result = {
        "broken_artifact_count": sum(not path.is_file() for path in required),
        "canonical_frame_hash_mismatch": frame_hash_mismatch,
        "canonical_frame_count": len(canonical["frames"]),
        "inventory_source_count": len(source_rows),
        "source_hash_mismatch": source_hash_mismatch,
    }
    result["valid"] = (
        result["broken_artifact_count"] == 0
        and not frame_hash_mismatch
        and not source_hash_mismatch
        and len(source_rows) == len(config.sources)
        and len(canonical["frames"]) == len(config.sources)
    )
    return result


def provenance_audit(
    config: ProjectConfig,
    artifacts: ReleaseArtifacts,
) -> dict[str, object]:
    study = config.paths.project_root / "study_methods.md"
    study_hash = sha256_file(study)
    assignment = _json(config.scene_generation.assignment_lock_path)
    scene_summary = _json(
        artifacts.scene_directory / "scene_generation_summary.json"
    )
    miniature_summary = _json(artifacts.miniature_directory / "summary.json")
    scene_provenance = pq.read_table(
        artifacts.scene_directory / "provenance.parquet"
    ).to_pandas()
    miniature_provenance = pq.read_table(
        artifacts.miniature_directory / "provenance.parquet"
    ).to_pandas()
    report_path = (
        config.paths.reports_dir
        / f"{artifacts.miniature_directory.name}_m1_8_miniature_dataset.json"
    )
    miniature_report = _json(report_path)
    read_only_inputs = miniature_report["summary"][
        "read_only_verification"
    ]["inputs"]
    checks = {
        "assignment_to_scene": set(
            scene_provenance["assignment_hash"].astype(str)
        )
        == {assignment["assignment_hash"]},
        "assignment_to_miniature": (
            miniature_summary["assignment_hash"]
            == assignment["assignment_hash"]
        ),
        "boundary_to_scene": set(
            scene_provenance["canonical_boundary_hash"].astype(str)
        )
        == {sha256_file(config.district_assignment.canonical_boundary_path)},
        "config_to_miniature": set(
            miniature_provenance["config_hash"].astype(str)
        )
        == {config.canonical_hash},
        "miniature_self_hash": set(
            miniature_provenance["miniature_content_hash"].astype(str)
        )
        == {miniature_summary["content_hash"]},
        "scene_to_miniature": (
            miniature_summary["scene_content_hash"]
            == scene_summary["scene_content_hash"]
        ),
        "study_hash_recorded": (
            read_only_inputs[str(study)]["sha256"] == study_hash
        ),
    }
    return {
        "checks": checks,
        "config_hash": config.canonical_hash,
        "study_methods_hash": study_hash,
        "valid": all(checks.values()),
    }


def _parquet_codecs(path: Path) -> set[str]:
    file = pq.ParquetFile(path)
    return {
        file.metadata.row_group(group).column(column).compression
        for group in range(file.metadata.num_row_groups)
        for column in range(file.metadata.row_group(group).num_columns)
    }


def storage_audit(
    reference: ReleaseArtifacts,
    replay: ReleaseArtifacts,
    output_root: Path,
) -> dict[str, object]:
    roots = [
        Path(value)
        for artifacts in (reference, replay)
        for value in artifacts.to_dict().values()
        if Path(value).is_dir()
    ]
    parquets = sorted(
        {
            path
            for root in roots
            for path in root.rglob("*.parquet")
        }
    )
    codec_failures = {
        str(path): sorted(_parquet_codecs(path))
        for path in parquets
        if _parquet_codecs(path) != {"ZSTD"}
    }
    json_failures: list[str] = []
    for root in roots:
        for path in root.rglob("*.json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                json_failures.append(str(path))
    miniature_geometry_columns = {
        str(path): pq.read_schema(path).names
        for artifacts in (reference, replay)
        for path in artifacts.miniature_directory.glob("*.parquet")
        if "geometry" in pq.read_schema(path).names
    }
    per_scene_pt = sorted(str(path) for path in output_root.rglob("*.pt"))
    result = {
        "json_parse_failure_count": len(json_failures),
        "miniature_geometry_column_count": len(miniature_geometry_columns),
        "parquet_codec_failure_count": len(codec_failures),
        "parquet_file_count": len(parquets),
        "per_scene_pt_count": len(per_scene_pt),
    }
    result["valid"] = (
        not json_failures
        and not miniature_geometry_columns
        and not codec_failures
        and not per_scene_pt
    )
    return result


def schema_audit(
    config: ProjectConfig,
    reference: ReleaseArtifacts,
    replay: ReleaseArtifacts,
) -> dict[str, object]:
    schema = load_canonical_schema(config.paths.canonical_schema)
    reference_manifest = canonical_content(reference.canonical_manifest)
    replay_manifest = canonical_content(replay.canonical_manifest)
    result = {
        "current_schema_sha256": schema.sha256,
        "reference_manifest_schema_sha256": reference_manifest["schema_sha256"],
        "reference_schema_match": (
            reference_manifest["schema_sha256"] == schema.sha256
        ),
        "replay_manifest_schema_sha256": replay_manifest["schema_sha256"],
        "replay_schema_match": replay_manifest["schema_sha256"] == schema.sha256,
        "yaml_parse": True,
    }
    result["valid"] = (
        result["reference_schema_match"] and result["replay_schema_match"]
    )
    return result


_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def repository_audit(project_root: Path) -> dict[str, object]:
    yaml_failures: list[str] = []
    for path in project_root.rglob("*.yaml"):
        if ".git" in path.parts:
            continue
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            yaml_failures.append(str(path))
    broken_links: list[dict[str, str]] = []
    for path in project_root.rglob("*.md"):
        if ".git" in path.parts:
            continue
        for raw in _MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
            target = raw.strip().split("#", 1)[0].strip("<>")
            if (
                target
                and "://" not in target
                and not target.startswith("mailto:")
                and not (path.parent / target).resolve().exists()
            ):
                broken_links.append({"document": str(path), "target": target})
    marker_hits: list[str] = []
    for root_name in ("src", "tests", "scripts", "configs"):
        for path in (project_root / root_name).rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".sh",
                ".yaml",
                ".yml",
            }:
                continue
            if path.suffix == ".py":
                try:
                    with path.open("rb") as stream:
                        comments = " ".join(
                            token.string
                            for token in tokenize.tokenize(stream.readline)
                            if token.type == tokenize.COMMENT
                        )
                except (OSError, tokenize.TokenError):
                    comments = ""
                text = comments
            else:
                text = path.read_text(encoding="utf-8")
            if re.search(r"\b(?:TODO|FIXME|placeholder)\b", text, re.I):
                marker_hits.append(str(path))
    import_failures: dict[str, str] = {}
    import scene

    for module in pkgutil.walk_packages(scene.__path__, prefix="scene."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # pragma: no cover - diagnostic boundary
            import_failures[module.name] = f"{type(exc).__name__}: {exc}"
    debug_prints: list[str] = []
    allowed_prints = {
        project_root / "src" / "scene" / "cli.py",
        project_root / "scripts" / "validate_m1_7_contracts.py",
    }
    for path in (project_root / "src").rglob("*.py"):
        if path in allowed_prints:
            continue
        if re.search(r"(?m)^\s*print\(", path.read_text(encoding="utf-8")):
            debug_prints.append(str(path))
    modules: dict[str, Path] = {}
    for path in (project_root / "src" / "scene").rglob("*.py"):
        relative = path.relative_to(project_root / "src").with_suffix("")
        module_name = ".".join(relative.parts)
        if module_name.endswith(".__init__"):
            module_name = module_name.removesuffix(".__init__")
        modules[module_name] = path
    imported_modules: set[str] = set()
    for base in (
        project_root / "src",
        project_root / "tests",
        project_root / "scripts",
    ):
        for path in base.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module)
    entry_modules = {"scene.__main__", "scene.cli"}
    unused_modules = [
        module
        for module, path in modules.items()
        if path.name != "__init__.py"
        and module not in entry_modules
        and not any(
            imported == module or imported.startswith(f"{module}.")
            for imported in imported_modules
        )
    ]
    result = {
        "broken_import_count": len(import_failures),
        "broken_markdown_link_count": len(broken_links),
        "dead_code_candidate_count": len(unused_modules),
        "debug_print_count": len(debug_prints),
        "placeholder_todo_count": len(marker_hits),
        "unused_module_count": len(unused_modules),
        "yaml_parse_failure_count": len(yaml_failures),
    }
    result["valid"] = not any(result.values())
    return result


def open_decisions(decision_log: Path) -> dict[str, object]:
    text = decision_log.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if re.match(r"\|\s*D-\d+", line) and line.rstrip().endswith("| Open |"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.append({"decision_id": cells[0], "topic": cells[1]})
    blockers = [
        row for row in rows
        if row["decision_id"] in {"D-004", "D-006", "D-012"}
    ]
    return {
        "m2_1_blocking": blockers,
        "m2_1_blocking_count": len(blockers),
        "open_count": len(rows),
        "open_decisions": rows,
    }
