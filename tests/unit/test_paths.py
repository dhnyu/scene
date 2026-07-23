from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_config_data, write_config
from scene.core.config import load_config
from scene.core.exceptions import PathValidationError
from scene.core.paths import create_output_directories, validate_paths


def _prepare_inputs(root: Path) -> None:
    (root / "inputs").mkdir()
    (root / "external").mkdir()


def test_read_only_inputs_and_outputs_are_separate(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    config = load_config(
        write_config(tmp_path / "project.yaml", make_config_data(tmp_path))
    )

    result = validate_paths(config.paths)

    assert result.read_only_roots == (
        (tmp_path / "inputs").resolve(),
        (tmp_path / "external").resolve(),
    )
    assert all(not path.exists() for path in result.output_directories)


def test_input_output_overlap_is_rejected(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    data = make_config_data(tmp_path)
    data["paths"]["output_root"] = str(tmp_path / "inputs" / "derived")
    config = load_config(write_config(tmp_path / "project.yaml", data))

    with pytest.raises(PathValidationError, match="overlap"):
        validate_paths(config.paths)


def test_output_outside_project_is_rejected(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    data = make_config_data(tmp_path)
    data["paths"]["reports_dir"] = str(tmp_path.parent / "reports")
    config = load_config(write_config(tmp_path / "project.yaml", data))

    with pytest.raises(PathValidationError, match="inside project_root"):
        validate_paths(config.paths)


def test_only_output_directories_are_created(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    config = load_config(
        write_config(tmp_path / "project.yaml", make_config_data(tmp_path))
    )

    created = create_output_directories(config.paths)

    assert all(path.is_dir() for path in created)
    assert set(created).isdisjoint(config.paths.read_only_roots)
