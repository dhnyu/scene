"""POI Adapter exception hierarchy."""

from scene.core.exceptions import SceneError


class POIError(SceneError):
    """Base class for M1.4.3 failures."""


class POIReaderError(POIError):
    """Raised when canonical POI inputs cannot be read safely."""


class POIValidationError(POIError):
    """Raised when an invalid POIDataset cannot be serialized."""


class POISerializationError(POIError):
    """Raised when a POIDataset artifact cannot be written."""
