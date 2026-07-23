from __future__ import annotations

from pathlib import Path

from scene.boundaries.adapter import adapt_seoul_districts
from scene.boundaries.reader import audit_boundary_source, read_seoul_features
from scene.boundaries.validator import validate_canonical_districts


SOURCE = Path(
    "/mnt/hdd002/fusedatalarge/geodata/koreanadm/koreanadm_2024_2Q.gpkg"
)


def test_actual_official_seoul_districts_read_only() -> None:
    before = SOURCE.stat()
    audit = audit_boundary_source(SOURCE)
    districts, sido = read_seoul_features(audit)
    dataset = adapt_seoul_districts(
        districts,
        sido,
        audit,
        source_name="koreanadm_2024q2_sigungu",
    )
    validation = validate_canonical_districts(dataset)
    after = SOURCE.stat()

    assert audit.sha256 == (
        "7bf8f220ba60696b218ecc6a7b49a577fb9bed8fcfc99af6fa7759f8fc250a4b"
    )
    assert audit.district_layer == "sigungu"
    assert audit.sido_layer == "sido"
    assert validation.valid
    assert validation.row_count == 25
    assert before.st_size == after.st_size
    assert before.st_mtime_ns == after.st_mtime_ns
