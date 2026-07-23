"""Convert validated M1.3 building frames into the BuildingDataset API."""

from __future__ import annotations

from dataclasses import dataclass

from scene.buildings.dataset import (
    BuildingAttributeFrame,
    BuildingDataset,
    BuildingGeometryFrame,
    CanonicalBuildingInput,
)
from scene.buildings.validator import BuildingValidationResult, BuildingValidator


@dataclass(frozen=True, slots=True)
class BuildingAdapterResult:
    """BuildingDataset paired with its non-throwing validation outcome."""

    dataset: BuildingDataset
    validation: BuildingValidationResult


class BuildingAdapter:
    """Construct the unjoined building API without changing canonical rows."""

    def __init__(self, validator: BuildingValidator) -> None:
        self._validator = validator

    def adapt(
        self,
        canonical_input: CanonicalBuildingInput,
    ) -> BuildingAdapterResult:
        validation = self._validator.validate(canonical_input)
        geometry = BuildingGeometryFrame(
            dataframe=canonical_input.geometry_table,
            crs=canonical_input.geometry_crs,
            geometry_type=canonical_input.geometry_type,
            bbox=validation.bbox,
            source_metadata=canonical_input.geometry_source,
            provenance_metadata=canonical_input.geometry_provenance,
        )
        attributes = BuildingAttributeFrame(
            dataframe=canonical_input.attribute_table,
            source_metadata=canonical_input.attribute_source,
            provenance_metadata=canonical_input.attribute_provenance,
        )
        return BuildingAdapterResult(
            dataset=BuildingDataset(
                geometry=geometry,
                attributes=attributes,
            ),
            validation=validation,
        )
