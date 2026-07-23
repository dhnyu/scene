from __future__ import annotations

import pytest

from scene.boundaries.workflow import MAPPING_ROWS, _inventory_preservation
from scene.cli import main


def test_boundary_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["boundary", "integrate-seoul-districts", "--help"])
    assert exc.value.code == 0
    assert "--config" in capsys.readouterr().out


def test_district_mapping_covers_required_canonical_fields() -> None:
    canonical_fields = {row[1] for row in MAPPING_ROWS}
    assert {
        "district_id",
        "district_code",
        "district_name",
        "sido_code",
        "sido_name",
        "source_name",
        "source_object_id",
        "source_path",
        "source_layer",
        "source_crs",
        "canonical_crs",
        "source_sha256",
        "geometry",
    }.issubset(canonical_fields)


def test_existing_inventory_preservation_diagnoses_changes() -> None:
    previous = {
        "source": {
            "source_path": "/read-only/source.gpkg",
            "sha256": "a" * 64,
            "feature_count": 25,
        }
    }
    unchanged = _inventory_preservation(previous, previous)
    changed = _inventory_preservation(
        previous,
        {
            "source": {
                "source_path": "/read-only/source.gpkg",
                "sha256": "b" * 64,
                "feature_count": 25,
            }
        },
    )

    assert unchanged["existing_source_missing_count"] == 0
    assert unchanged["existing_source_metadata_changed_count"] == 0
    assert changed["existing_source_metadata_changed"] == {
        "source": ["sha256"]
    }
