"""Project-specific exception hierarchy."""


class SceneError(Exception):
    """Base class for expected project failures."""


class ConfigurationError(SceneError):
    """Raised when configuration is malformed or unsupported."""


class PathValidationError(SceneError):
    """Raised when path roles overlap or required paths are invalid."""


class RunContextError(SceneError):
    """Raised when run metadata cannot be constructed safely."""


class ReportingError(SceneError):
    """Raised when a report cannot be serialized or written."""
