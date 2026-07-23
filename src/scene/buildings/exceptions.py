"""Building Adapter exception hierarchy."""

from scene.core.exceptions import SceneError


class BuildingError(SceneError):
    """Base class for M1.4.1 failures."""


class BuildingReaderError(BuildingError):
    """Raised when canonical building inputs cannot be read safely."""


class BuildingValidationError(BuildingError):
    """Raised when an invalid BuildingDataset cannot be serialized."""


class BuildingSerializationError(BuildingError):
    """Raised when a BuildingDataset artifact cannot be written."""
