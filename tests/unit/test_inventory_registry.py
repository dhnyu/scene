from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_config_data, write_config
from scene.core.config import load_config
from scene.core.exceptions import ConfigurationError
from scene.inventory.exceptions import RegistryError
from scene.inventory.registry import SourceDescriptor, SourceRegistry


def test_registry_is_built_from_typed_config(tmp_path: Path) -> None:
    data = make_config_data(tmp_path)
    data["sources"] = [
        {
            "source_name": "buildings",
            "category": "buildings",
            "kind": "vector",
            "path": "buildings.gpkg",
            "layer": "buildings",
        },
        {
            "source_name": "dem",
            "category": "dem",
            "kind": "raster",
            "path": "dem.tif",
        },
    ]
    config = load_config(write_config(tmp_path / "project.yaml", data))

    registry = SourceRegistry.from_project_config(config)

    assert len(registry) == 2
    assert registry.get("buildings").layer == "buildings"
    assert registry.get("dem").path == (tmp_path / "inputs" / "dem.tif").resolve()


def test_duplicate_registry_name_is_rejected(tmp_path: Path) -> None:
    source = SourceDescriptor(
        source_name="roads",
        category="roads",
        kind="vector",
        path=tmp_path / "roads.gpkg",
        layer="roads",
    )

    with pytest.raises(RegistryError, match="duplicate"):
        SourceRegistry((source, source))


def test_vector_source_without_layer_is_rejected_by_config(
    tmp_path: Path,
) -> None:
    data = make_config_data(tmp_path)
    data["sources"] = [
        {
            "source_name": "roads",
            "category": "roads",
            "kind": "vector",
            "path": "roads.gpkg",
        }
    ]

    with pytest.raises(ConfigurationError, match="layer is required"):
        load_config(write_config(tmp_path / "project.yaml", data))


def test_source_path_outside_input_root_is_rejected(tmp_path: Path) -> None:
    data = make_config_data(tmp_path)
    data["sources"] = [
        {
            "source_name": "dem",
            "category": "dem",
            "kind": "raster",
            "path": str(tmp_path / "outside.tif"),
        }
    ]

    with pytest.raises(ConfigurationError, match="inside paths.input_root"):
        load_config(write_config(tmp_path / "project.yaml", data))


def test_registry_preserves_boundary_adapter_metadata(tmp_path: Path) -> None:
    data = make_config_data(tmp_path)
    data["sources"] = [
        {
            "source_name": "official_districts",
            "category": "administrative_boundaries",
            "kind": "vector",
            "path": str(tmp_path / "official.gpkg"),
            "layer": "sigungu",
            "source_format": "geopackage",
            "source_crs": "EPSG:5179",
            "administrative_level": "sigungu",
            "geographic_scope": "south_korea",
            "expected_geometry_type": "MultiPolygon",
            "expected_feature_count": 252,
            "read_only": True,
            "canonical_adapter": "seoul_district_boundary",
        }
    ]
    config = load_config(write_config(tmp_path / "project.yaml", data))

    source = SourceRegistry.from_project_config(config).get(
        "official_districts"
    )

    assert source.administrative_level == "sigungu"
    assert source.expected_feature_count == 252
    assert source.canonical_adapter == "seoul_district_boundary"
