"""Canonical schema workflow exceptions."""

from scene.core.exceptions import SceneError


class CanonicalSchemaError(SceneError):
    """Base class for M1.3 schema and mapping failures."""


class SchemaDefinitionError(CanonicalSchemaError):
    """Raised when the authoritative canonical schema is malformed."""


class SourceMappingError(CanonicalSchemaError):
    """Raised when a source cannot be mapped to its declared frame."""


class CanonicalSerializationError(CanonicalSchemaError):
    """Raised when a canonical artifact cannot be written safely."""
