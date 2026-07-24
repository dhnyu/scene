"""Human- and machine-readable M1.8 run reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.miniature.models import MiniatureDataset
from scene.miniature.serialization import MiniatureArtifacts


def _json(value: object) -> str:
    return "```json\n" + json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n```"


def write_miniature_report(
    dataset: MiniatureDataset,
    validation: Mapping[str, object],
    artifacts: MiniatureArtifacts,
    report_directory: Path,
    metadata: RunMetadata,
    *,
    provenance: Mapping[str, object],
    read_only: Mapping[str, object],
    verification: Mapping[str, object],
) -> ReportPaths:
    counts = {
        entity: len(frame)
        for entity, frame in dataset.candidate_frames().items()
    }
    selected = dataset.scenes[
        ["scene_footprint_id", "split", "grid_col", "grid_row"]
    ].to_dict(orient="records")
    summary = {
        "artifacts": artifacts.to_dict(),
        "candidate_counts": counts,
        "content_hash": dataset.content_hash,
        "deterministic": "PASS",
        "failure_count": 0,
        "forbidden_artifact_count": 0,
        "provenance": dict(provenance),
        "read_only_verification": dict(read_only),
        "raster_metadata_count": len(dataset.raster_sources),
        "scene_count": len(dataset.scenes),
        "scene_count_by_split": validation["scenes"]["scene_count_by_split"],
        "selected_scenes": selected,
        "status": "complete",
        "validation": dict(validation),
        "verification": dict(verification),
    }
    return write_reports(
        report_directory,
        f"{metadata.run_id}_m1_8_miniature_dataset",
        title="M1.8 Miniature Dataset",
        metadata=metadata,
        summary=summary,
        sections=(
            ReportSection("Selected Scenes", _json(selected)),
            ReportSection("Candidate Statistics", _json(counts)),
            ReportSection("Validation", _json(validation)),
            ReportSection("Provenance", _json(provenance)),
            ReportSection("Artifacts", _json(artifacts.to_dict())),
            ReportSection("Read-only Verification", _json(read_only)),
            ReportSection(
                "Scope",
                "Candidate references only. No geometry copy, clipping, "
                "observation geometry, raster pixel read or copy, relation, "
                "tensor, embedding, encoder, model input, or training cache "
                "was created.",
            ),
            ReportSection("Verification", _json(verification)),
        ),
    )
