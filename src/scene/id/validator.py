"""Validation for stable IDs, mappings, and provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from scene.id.generator import DerivedIdFactory
from scene.id.provenance import ENTITY_SPECS, StableIdDataset


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_REQUIRED = (
    "source_name",
    "source_object_id",
    "canonical_object_id",
    "source_path",
    "source_sha256",
    "schema_version",
    "run_id",
    "config_hash",
)


@dataclass(frozen=True, slots=True)
class EntityIdValidation:
    entity_type: str
    id_name: str
    row_count: int
    null_id_count: int
    empty_id_count: int
    duplicate_id_count: int
    source_native_duplicate_count: int
    format_valid: bool

    @property
    def valid(self) -> bool:
        return (
            self.null_id_count == 0
            and self.empty_id_count == 0
            and self.duplicate_id_count == 0
            and self.source_native_duplicate_count == 0
            and self.format_valid
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["valid"] = self.valid
        return value


@dataclass(frozen=True, slots=True)
class StableIdValidation:
    entities: tuple[EntityIdValidation, ...]
    row_count: int
    global_duplicate_id_count: int
    global_null_id_count: int
    deterministic_regeneration: bool
    source_canonical_mapping_valid: bool
    provenance_complete: bool
    provenance_missing_count: int
    provenance_row_count_matches: bool

    @property
    def valid(self) -> bool:
        return (
            all(entity.valid for entity in self.entities)
            and self.global_duplicate_id_count == 0
            and self.global_null_id_count == 0
            and self.deterministic_regeneration
            and self.source_canonical_mapping_valid
            and self.provenance_complete
            and self.provenance_missing_count == 0
            and self.provenance_row_count_matches
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            entity.entity_type: entity.row_count
            for entity in self.entities
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "deterministic_regeneration": self.deterministic_regeneration,
            "entities": [entity.to_dict() for entity in self.entities],
            "global_duplicate_id_count": self.global_duplicate_id_count,
            "global_null_id_count": self.global_null_id_count,
            "provenance_complete": self.provenance_complete,
            "provenance_missing_count": self.provenance_missing_count,
            "provenance_row_count_matches": self.provenance_row_count_matches,
            "row_count": self.row_count,
            "source_canonical_mapping_valid": (
                self.source_canonical_mapping_valid
            ),
            "valid": self.valid,
        }


def _count_empty(array: pa.ChunkedArray) -> int:
    return pc.sum(pc.equal(array, "")).as_py() or 0


def _duplicate_count(array: pa.ChunkedArray) -> int:
    non_null = len(array) - array.null_count
    return non_null - int(pc.count_distinct(array).as_py())


def _filter(table: pa.Table, entity_type: str) -> pa.Table:
    return table.filter(pc.equal(table["entity_type"], entity_type))


class StableIdValidator:
    """Validate materialized IDs without changing or dropping rows."""

    def validate(
        self,
        dataset: StableIdDataset,
        *,
        regeneration_digest: str,
    ) -> StableIdValidation:
        ids = dataset.ids
        provenance = dataset.provenance
        entities: list[EntityIdValidation] = []
        for spec in ENTITY_SPECS:
            table = _filter(ids, spec.entity_type)
            canonical = table["canonical_object_id"]
            native = table["source_native_id"]
            formats = [
                isinstance(value, str) and _SHA256.fullmatch(value) is not None
                for value in canonical.to_pylist()
            ]
            entities.append(
                EntityIdValidation(
                    entity_type=spec.entity_type,
                    id_name=spec.id_name,
                    row_count=table.num_rows,
                    null_id_count=canonical.null_count,
                    empty_id_count=_count_empty(canonical),
                    duplicate_id_count=_duplicate_count(canonical),
                    source_native_duplicate_count=_duplicate_count(native),
                    format_valid=all(formats),
                )
            )

        canonical = ids["canonical_object_id"]
        source_ids = ids["source_object_id"]
        provenance_missing = 0
        for field in _PROVENANCE_REQUIRED:
            column = provenance[field]
            provenance_missing += column.null_count + _count_empty(column)
        source_canonical_mapping_valid = (
            canonical.equals(source_ids)
            and provenance["canonical_object_id"].equals(canonical)
            and provenance["source_object_id"].equals(source_ids)
            and provenance["source_native_id"].equals(
                ids["source_native_id"]
            )
        )
        return StableIdValidation(
            entities=tuple(entities),
            row_count=ids.num_rows,
            global_duplicate_id_count=_duplicate_count(canonical),
            global_null_id_count=canonical.null_count + source_ids.null_count,
            deterministic_regeneration=(
                dataset.generation_digest == regeneration_digest
            ),
            source_canonical_mapping_valid=source_canonical_mapping_valid,
            provenance_complete=provenance_missing == 0,
            provenance_missing_count=provenance_missing,
            provenance_row_count_matches=(
                ids.num_rows == provenance.num_rows
                and ids.num_rows == dataset.source.expected_row_count
            ),
        )


class DerivedIdValidator:
    """Validate future ID factories without materializing scene-based rows."""

    @staticmethod
    def scene_identity_is_deterministic(
        *,
        scene_generation_version: str,
        canonical_crs: str,
        origin_x: int | float | str,
        origin_y: int | float | str,
        scene_width: int | float | str,
        scene_height: int | float | str,
        stride_x: int | float | str,
        stride_y: int | float | str,
        grid_col: int,
        grid_row: int,
    ) -> bool:
        arguments = (
            scene_generation_version,
            canonical_crs,
            origin_x,
            origin_y,
            scene_width,
            scene_height,
            stride_x,
            stride_y,
            grid_col,
            grid_row,
        )
        first = DerivedIdFactory.scene_footprint_id(*arguments)
        second = DerivedIdFactory.scene_footprint_id(*arguments)
        return first == second and DerivedIdFactory.scene_id(first) == first

    @staticmethod
    def clip_component_order_is_invariant(
        components: tuple[tuple[str, bytes], ...],
    ) -> bool:
        forward = DerivedIdFactory.clip_part_ids(components)
        reverse = DerivedIdFactory.clip_part_ids(reversed(components))
        return forward == reverse

    @staticmethod
    def relation_views_are_disjoint(
        *,
        scene_id: str,
        geometry_version: str,
        src_scene_object_id: str,
        dst_scene_object_id: str,
        relation_type: str,
    ) -> bool:
        contexts = (
            DerivedIdFactory.relation_context_id(
                scene_id,
                "original",
                None,
                geometry_version,
            ),
            DerivedIdFactory.relation_context_id(
                scene_id,
                "augmented",
                1,
                geometry_version,
            ),
            DerivedIdFactory.relation_context_id(
                scene_id,
                "augmented",
                2,
                geometry_version,
            ),
        )
        relations = {
            DerivedIdFactory.relation_id(
                context,
                src_scene_object_id,
                dst_scene_object_id,
                relation_type,
            )
            for context in contexts
        }
        return len(set(contexts)) == 3 and len(relations) == 3
