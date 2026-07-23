"""Raster Adapter exception hierarchy."""

from scene.core.exceptions import SceneError


class RasterError(SceneError):
    """Base class for M1.4.4 failures."""


class RasterReaderError(RasterError):
    """Raised when registered raster metadata cannot be read safely."""


class RasterValidationError(RasterError):
    """Raised when invalid raster metadata cannot be serialized."""


class RasterSerializationError(RasterError):
    """Raised when raster metadata artifacts cannot be written."""
