from __future__ import annotations

import hashlib
from pathlib import Path

from scene.inventory.hashing import sha256_file


def test_full_sha256_matches_known_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 1_000_000)

    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    assert sha256_file(source, chunk_size=4096) == expected
