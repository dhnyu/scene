"""Convert validated M1.3 POI frames into the POIDataset API."""

from __future__ import annotations

from dataclasses import dataclass

from scene.pois.category import append_category_path
from scene.pois.dataset import (
    CanonicalPOIInput,
    POIAttributeFrame,
    POIDataset,
    POIGeometryFrame,
)
from scene.pois.validator import POIValidationResult, POIValidator


@dataclass(frozen=True, slots=True)
class POIAdapterResult:
    """POIDataset paired with its non-throwing validation outcome."""

    dataset: POIDataset
    validation: POIValidationResult


class POIAdapter:
    """Construct the unjoined POI API without changing source rows."""

    def __init__(self, validator: POIValidator) -> None:
        self._validator = validator

    def adapt(self, canonical_input: CanonicalPOIInput) -> POIAdapterResult:
        validation = self._validator.validate(canonical_input)
        dataset = POIDataset(
            geometry=POIGeometryFrame(
                dataframe=canonical_input.geometry_table,
                crs=canonical_input.geometry_crs,
                geometry_type=canonical_input.geometry_type,
                bbox=validation.bbox,
                source_metadata=canonical_input.geometry_source,
                provenance_metadata=canonical_input.geometry_provenance,
            ),
            attributes=POIAttributeFrame(
                dataframe=append_category_path(
                    canonical_input.attribute_table
                ),
                source_metadata=canonical_input.attribute_source,
                provenance_metadata=canonical_input.attribute_provenance,
            ),
            source_join_key_metadata=validation.join_key,
        )
        return POIAdapterResult(dataset=dataset, validation=validation)
