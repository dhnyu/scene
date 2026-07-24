"""Expected M1.9 validation failures."""

from scene.core.exceptions import SceneError


class ReleaseValidationError(SceneError):
    """Raised when the release validation cannot complete safely."""
