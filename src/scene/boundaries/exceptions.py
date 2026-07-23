"""Expected failures for administrative-boundary integration."""

from scene.core.exceptions import SceneError


class BoundaryIntegrationError(SceneError):
    """Raised when a contracted boundary invariant cannot be satisfied."""
