"""M4 representation-learning stages."""

from __future__ import annotations

from typing import Any

from scene.m4.schemas import M4_STAGE_IDS


def run_m4_stage(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Lazy workflow import so worker submodules can run with `python -m`."""

    from scene.m4.workflow import run_m4_stage as _run_m4_stage

    return _run_m4_stage(*args, **kwargs)


__all__ = ["M4_STAGE_IDS", "run_m4_stage"]
