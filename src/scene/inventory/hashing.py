"""Streaming source hashes that never modify or materialize input data."""

from __future__ import annotations

import hashlib
from pathlib import Path


DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


def sha256_file(
    path: str | Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Compute a full lowercase SHA-256 using bounded memory."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    buffer = bytearray(chunk_size)
    view = memoryview(buffer)
    with Path(path).open("rb", buffering=0) as stream:
        while count := stream.readinto(buffer):
            digest.update(view[:count])
    return digest.hexdigest()
