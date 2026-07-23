"""Extensible source registry constructed only from typed configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from scene.core.config import ProjectConfig, SourceConfig
from scene.inventory.exceptions import RegistryError


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Immutable source registration used by all later milestones."""

    source_name: str
    category: str
    kind: str
    path: Path
    layer: str | None = None

    @classmethod
    def from_config(cls, source: SourceConfig) -> SourceDescriptor:
        return cls(
            source_name=source.source_name,
            category=source.category,
            kind=source.kind,
            path=source.path,
            layer=source.layer,
        )


class SourceRegistry:
    """Ordered collection with unique names and lookup by registration name."""

    def __init__(self, sources: tuple[SourceDescriptor, ...]) -> None:
        if not sources:
            raise RegistryError("source registry must contain at least one source")
        by_name: dict[str, SourceDescriptor] = {}
        for source in sources:
            if source.source_name in by_name:
                raise RegistryError(
                    f"duplicate source_name: {source.source_name}"
                )
            if source.kind == "vector" and source.layer is None:
                raise RegistryError(
                    f"vector source requires layer: {source.source_name}"
                )
            by_name[source.source_name] = source
        self._sources = sources
        self._by_name = by_name

    @classmethod
    def from_project_config(cls, config: ProjectConfig) -> SourceRegistry:
        descriptors = tuple(
            SourceDescriptor.from_config(source)
            for source in config.sources
        )
        return cls(descriptors)

    def __iter__(self) -> Iterator[SourceDescriptor]:
        return iter(self._sources)

    def __len__(self) -> int:
        return len(self._sources)

    def get(self, source_name: str) -> SourceDescriptor:
        try:
            return self._by_name[source_name]
        except KeyError as exc:
            raise RegistryError(
                f"source is not registered: {source_name}"
            ) from exc
