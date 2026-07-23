"""Path-role validation for read-only inputs and project-owned outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scene.core.config import PathConfig
from scene.core.exceptions import PathValidationError


@dataclass(frozen=True, slots=True)
class PathValidationResult:
    """Validated path roles without filesystem mutation."""

    read_only_roots: tuple[Path, ...]
    output_directories: tuple[Path, ...]


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or child.is_relative_to(parent)


def validate_paths(
    paths: PathConfig,
    *,
    require_input_roots: bool = True,
) -> PathValidationResult:
    """Validate path existence and prevent input/output overlap."""

    if not paths.project_root.is_dir():
        raise PathValidationError(
            f"project_root is not an existing directory: {paths.project_root}"
        )

    for input_root in paths.read_only_roots:
        if require_input_roots and not input_root.is_dir():
            raise PathValidationError(
                f"read-only input root is not an existing directory: {input_root}"
            )

    for output in paths.output_directories:
        if not _contains(paths.project_root, output):
            raise PathValidationError(
                f"output path must be inside project_root: {output}"
            )
        for input_root in paths.read_only_roots:
            if _contains(input_root, output) or _contains(output, input_root):
                raise PathValidationError(
                    "read-only input and output paths overlap: "
                    f"input={input_root}, output={output}"
                )

    return PathValidationResult(
        read_only_roots=paths.read_only_roots,
        output_directories=paths.output_directories,
    )


def create_output_directories(paths: PathConfig) -> tuple[Path, ...]:
    """Create only project-owned output directories after validation."""

    validate_paths(paths)
    created: list[Path] = []
    for directory in paths.output_directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PathValidationError(
                f"cannot create output directory {directory}: {exc}"
            ) from exc
        created.append(directory)
    return tuple(created)
