from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scene.schema.exceptions import SchemaDefinitionError
from scene.schema.schema import load_canonical_schema


def test_project_canonical_schema_loads_all_registered_frames() -> None:
    root = Path(__file__).resolve().parents[2]

    schema = load_canonical_schema(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    )

    assert schema.schema_version == "1.1.0"
    assert schema.canonical_crs == "EPSG:5186"
    assert len(schema.source_frames) == 10
    assert schema.frame_for("seoul_buildings_attributes").frame_name == (
        "building_attribute"
    )
    assert len(schema.sha256) == 64


def test_malformed_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "schema.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_name": "fixture",
                "schema_version": "1",
                "canonical_crs": {"epsg": 5186},
                "m1_3_canonical_frames": {
                    "fixture": {
                        "frame_name": "fixture",
                        "source_kind": "tabular",
                        "columns": [
                            {
                                "column": "value",
                                "source_column": "VALUE",
                                "dtype": "unsupported",
                                "nullable": False,
                                "description": "fixture",
                                "source": "fixture.VALUE",
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SchemaDefinitionError, match="unsupported"):
        load_canonical_schema(path)


def test_missing_source_frame_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    schema = load_canonical_schema(
        root / "docs" / "contracts" / "canonical_schema.yaml"
    )

    with pytest.raises(SchemaDefinitionError, match="no M1.3 canonical frame"):
        schema.frame_for("not_registered")
