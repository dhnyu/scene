"""Road Adapter exception hierarchy."""

from scene.core.exceptions import SceneError


class RoadError(SceneError):
    """Base class for M1.4.2 failures."""


class RoadReaderError(RoadError):
    """Raised when canonical road inputs cannot be read safely."""


class RoadValidationError(RoadError):
    """Raised when invalid road datasets cannot be serialized."""


class RoadSerializationError(RoadError):
    """Raised when a road artifact cannot be written."""
