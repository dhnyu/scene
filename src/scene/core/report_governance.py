"""Report governance helpers for canonical/raw milestone reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scene.core.reporting import ReportPaths, ReportSection, write_reports
from scene.core.run_context import RunMetadata


@dataclass(frozen=True, slots=True)
class GovernedReportPaths:
    """Raw and canonical report paths under docs/reports."""

    raw: ReportPaths
    canonical_markdown: Path


def write_milestone_reports(
    project_root: Path,
    milestone_group: str,
    raw_basename: str,
    canonical_name: str,
    *,
    title: str,
    metadata: RunMetadata,
    summary: Mapping[str, Any],
    sections: Sequence[ReportSection] = (),
) -> GovernedReportPaths:
    """Write timestamped raw reports and a timestamp-free canonical Markdown."""

    report_root = project_root / "docs" / "reports" / milestone_group
    raw_paths = write_reports(
        report_root / "raw",
        raw_basename,
        title=title,
        metadata=metadata,
        summary=summary,
        sections=sections,
    )
    canonical_directory = report_root / "canonical"
    canonical_directory.mkdir(parents=True, exist_ok=True)
    canonical_markdown = canonical_directory / canonical_name
    raw_relative = Path("..") / "raw" / raw_paths.markdown.name
    raw_content = raw_paths.markdown.read_text(encoding="utf-8")
    first_line, _, remainder = raw_content.partition("\n")
    canonical_content = (
        f"{first_line}\n\n"
        f"Canonical report. Raw source: [`{raw_paths.markdown.name}`]"
        f"({raw_relative.as_posix()}).\n\n"
        f"{remainder.lstrip()}"
    )
    canonical_markdown.write_text(canonical_content, encoding="utf-8")
    return GovernedReportPaths(
        raw=raw_paths,
        canonical_markdown=canonical_markdown,
    )
