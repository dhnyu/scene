"""Markdown and JSON report serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from scene.core.exceptions import ReportingError
from scene.core.run_context import RunMetadata


_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class ReportSection:
    """One Markdown report section."""

    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class ReportPaths:
    markdown: Path
    json: Path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_reports(
    report_dir: str | Path,
    basename: str,
    *,
    title: str,
    metadata: RunMetadata,
    summary: Mapping[str, Any],
    sections: Sequence[ReportSection] = (),
) -> ReportPaths:
    """Write matching human-readable and machine-readable run reports."""

    if _SAFE_BASENAME.fullmatch(basename) is None:
        raise ReportingError(
            "report basename may contain only letters, digits, '.', '_' and '-'"
        )
    if not title.strip():
        raise ReportingError("report title must not be empty")

    directory = Path(report_dir)
    markdown_path = directory / f"{basename}.md"
    json_path = directory / f"{basename}.json"
    payload = {
        "metadata": metadata.to_dict(),
        "summary": dict(summary),
        "title": title,
    }

    metadata_rows = "\n".join(
        f"| `{key}` | `{str(value).replace('|', '\\|')}` |"
        for key, value in metadata.to_dict().items()
    )
    section_text = "\n".join(
        f"\n## {section.heading.strip()}\n\n{section.body.rstrip()}\n"
        for section in sections
    )
    markdown = (
        f"# {title.strip()}\n\n"
        "## Run Metadata\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"{metadata_rows}\n"
        f"{section_text}"
    )

    try:
        serialized_json = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(markdown_path, markdown)
        _atomic_write(json_path, serialized_json)
    except (OSError, TypeError, ValueError) as exc:
        raise ReportingError(f"cannot write report {basename}: {exc}") from exc

    return ReportPaths(markdown=markdown_path, json=json_path)
