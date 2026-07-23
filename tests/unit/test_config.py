from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from conftest import make_config_data, write_config
from scene.core.config import load_config, write_resolved_config
from scene.core.exceptions import ConfigurationError


def test_load_valid_config(tmp_path: Path) -> None:
    data = make_config_data(tmp_path)
    path = write_config(tmp_path / "project.yaml", data)

    config = load_config(path)

    assert config.project_name == "scene-test"
    assert config.paths.input_root == (tmp_path / "inputs").resolve()
    assert config.storage.parquet_compression == "zstd"
    assert len(config.canonical_hash) == 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.pop("timezone"), "missing required keys"),
        (lambda value: value.update({"unknown": True}), "unknown keys"),
        (lambda value: value.update({"project_name": 3}), "non-empty string"),
        (
            lambda value: value["storage"].update(
                {"parquet_compression": "snappy"}
            ),
            "must be 'zstd'",
        ),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    data = make_config_data(tmp_path)
    mutate(data)
    path = write_config(tmp_path / "invalid.yaml", data)

    with pytest.raises(ConfigurationError, match=message):
        load_config(path)


def test_relative_and_home_paths_are_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    data = make_config_data(tmp_path)
    data["paths"]["project_root"] = ".."
    data["paths"]["input_root"] = "~/source"
    path = write_config(config_dir / "project.yaml", data)

    config = load_config(path)

    assert config.paths.project_root == tmp_path.resolve()
    assert config.paths.input_root == (home / "source").resolve()


def test_config_hash_is_deterministic_across_yaml_key_order(
    tmp_path: Path,
) -> None:
    data = make_config_data(tmp_path)
    first = write_config(tmp_path / "first.yaml", data)
    reversed_data = dict(reversed(list(deepcopy(data).items())))
    second = write_config(tmp_path / "second.yaml", reversed_data)

    assert load_config(first).canonical_hash == load_config(second).canonical_hash


def test_write_resolved_config_includes_hash(tmp_path: Path) -> None:
    data = make_config_data(tmp_path)
    config = load_config(write_config(tmp_path / "project.yaml", data))

    destination = write_resolved_config(
        config,
        tmp_path / "resolved" / "project.yaml",
    )
    resolved = yaml.safe_load(destination.read_text(encoding="utf-8"))

    assert resolved["resolved_config_hash"] == config.canonical_hash
    assert resolved["resolved_config"]["storage"]["geometry_format"] == "geopackage"
