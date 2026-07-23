"""Inventory-specific failures that do not represent source validation rows."""

from scene.core.exceptions import SceneError


class InventoryError(SceneError):
    """Base class for inventory workflow failures."""


class RegistryError(InventoryError):
    """Raised when the configured registry itself is invalid."""


class MetadataExtractionError(InventoryError):
    """Raised by a metadata backend and captured by the scanner."""


class InventorySerializationError(InventoryError):
    """Raised when inventory artifacts cannot be serialized."""
