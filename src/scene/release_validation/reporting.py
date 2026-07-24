"""M1.9 Markdown and JSON release-candidate report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata
from scene.release_validation.serialization import ReleaseValidationArtifacts


def _json(value: object) -> str:
    return "```json\n" + json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n```"


def write_release_report(
    report_directory: Path,
    metadata: RunMetadata,
    *,
    payloads: Mapping[str, object],
    artifacts: ReleaseValidationArtifacts,
    read_only: Mapping[str, object],
    verification: Mapping[str, object],
) -> ReportPaths:
    release = payloads["release_summary"]
    summary = {
        "artifacts": artifacts.to_dict(),
        "failure_count": release["failure_count"],
        "m2_readiness": release["m2_readiness"],
        "open_decisions": release["open_decisions"],
        "read_only": dict(read_only),
        "release_candidate": release["release_candidate"],
        "release_categories": release["release_categories"],
        "status": "complete",
        "verification": dict(verification),
    }
    return write_reports(
        report_directory,
        f"{metadata.run_id}_m1_9_release_validation",
        title="M1.9 End-to-End Pipeline Validation",
        metadata=metadata,
        summary=summary,
        sections=(
            ReportSection("Pipeline Replay", _json(payloads["pipeline_replay"])),
            ReportSection("Geometry Audit", _json(payloads["geometry_audit"])),
            ReportSection("ID Audit", _json(payloads["id_audit"])),
            ReportSection("Manifest Audit", _json(payloads["manifest_audit"])),
            ReportSection(
                "Provenance Audit",
                _json(payloads["provenance_audit"]),
            ),
            ReportSection(
                "Repository Audit",
                _json(payloads["repository_audit"]),
            ),
            ReportSection("Performance", _json(payloads["performance"])),
            ReportSection("Release Candidate", _json(release)),
            ReportSection(
                "M2 Readiness",
                _json(
                    {
                        "m2_readiness": release["m2_readiness"],
                        "open_decisions": release["open_decisions"],
                    }
                ),
            ),
            ReportSection("Read-only Verification", _json(read_only)),
            ReportSection("Verification", _json(verification)),
        ),
    )
