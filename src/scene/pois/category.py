"""Vectorized, reversible POI category-path construction."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc


CATEGORY_COLUMNS = tuple(f"poi_category_{level}" for level in range(1, 7))
CATEGORY_PATH_COLUMN = "poi_category_path"
CATEGORY_PATH_SEPARATOR = " > "
CATEGORY_PATH_NULL_TOKEN = r"\N"
CATEGORY_NORMALIZATION = "identity"


def _encode_label(values: pa.ChunkedArray | pa.Array) -> pa.ChunkedArray:
    escaped = pc.replace_substring(pc.cast(values, pa.string()), "\\", "\\\\")
    escaped = pc.replace_substring(
        escaped,
        CATEGORY_PATH_SEPARATOR,
        r"\ > ",
    )
    return pc.fill_null(escaped, CATEGORY_PATH_NULL_TOKEN)


def build_category_path(table: pa.Table) -> pa.ChunkedArray:
    """Build a six-stage path without changing source category labels."""

    missing = [
        column
        for column in CATEGORY_COLUMNS
        if column not in table.column_names
    ]
    if missing:
        raise KeyError(f"missing category columns: {missing}")
    encoded = [_encode_label(table[column]) for column in CATEGORY_COLUMNS]
    path = encoded[0]
    for values in encoded[1:]:
        path = pc.binary_join_element_wise(
            path,
            values,
            CATEGORY_PATH_SEPARATOR,
        )
    return path


def append_category_path(table: pa.Table) -> pa.Table:
    """Return a new attribute frame with one derived path column."""

    path = build_category_path(table)
    field = pa.field(
        CATEGORY_PATH_COLUMN,
        pa.string(),
        nullable=False,
        metadata={
            b"description": b"reversible six-stage POI category path",
            b"normalization": CATEGORY_NORMALIZATION.encode("ascii"),
            b"separator": CATEGORY_PATH_SEPARATOR.encode("ascii"),
            b"source_columns": ",".join(CATEGORY_COLUMNS).encode("ascii"),
            b"source_null_token": CATEGORY_PATH_NULL_TOKEN.encode("ascii"),
        },
    )
    return table.append_column(field, path)
