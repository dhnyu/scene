"""Expected M2.1 observation contract failures."""

from scene.core.exceptions import SceneError


class ObservationContractError(SceneError):
    """Raised when the observation schema or fixture violates its contract."""


class ObservationGeometryError(ObservationContractError):
    """Raised for invalid, unsupported, or out-of-contract geometry."""
