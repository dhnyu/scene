"""Stable ID exception hierarchy."""

from scene.core.exceptions import SceneError


class StableIdError(SceneError):
    """Base class for M1.5 failures."""


class StableIdReaderError(StableIdError):
    """Raised when canonical ID inputs fail integrity checks."""


class StableIdGenerationError(StableIdError):
    """Raised when an ID cannot be generated from its contracted inputs."""


class StableIdSerializationError(StableIdError):
    """Raised when ID artifacts cannot be serialized."""
