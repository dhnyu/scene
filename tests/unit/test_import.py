from __future__ import annotations

import importlib
import logging
from pathlib import Path


def test_package_import_has_no_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    root_handlers = tuple(logging.getLogger().handlers)
    before = tuple(tmp_path.iterdir())

    package = importlib.import_module("scene")
    core = importlib.import_module("scene.core")

    assert package.__version__ == "0.1.0"
    assert core.ProjectConfig is not None
    assert tuple(logging.getLogger().handlers) == root_handlers
    assert tuple(tmp_path.iterdir()) == before
