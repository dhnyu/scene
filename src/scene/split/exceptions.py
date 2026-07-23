"""Expected M1.6 district-assignment failures."""

from scene.core.exceptions import SceneError


class DistrictAssignmentError(SceneError):
    """Raised when an immutable split contract cannot be satisfied."""
