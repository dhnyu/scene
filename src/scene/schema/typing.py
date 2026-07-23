"""Arrow dtype definitions for canonical frames."""

from __future__ import annotations

import pyarrow as pa

from scene.schema.exceptions import SchemaDefinitionError


_ARROW_TYPES: dict[str, pa.DataType] = {
    "binary": pa.binary(),
    "bool": pa.bool_(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "string": pa.string(),
    "uint8": pa.uint8(),
}


def arrow_type(dtype: str) -> pa.DataType:
    """Resolve a contract dtype to its exact Arrow storage type."""

    try:
        return _ARROW_TYPES[dtype]
    except KeyError as exc:
        raise SchemaDefinitionError(
            f"unsupported M1.3 canonical dtype: {dtype}"
        ) from exc
