"""Scene-footprint workflow exceptions."""

from scene.core.exceptions import SceneError


class SceneFootprintError(SceneError):
    """Raised when an approved M1.7 invariant cannot be satisfied."""
