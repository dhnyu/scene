from __future__ import annotations

import pyarrow as pa

from scene.pois.category import (
    CATEGORY_COLUMNS,
    CATEGORY_PATH_COLUMN,
    append_category_path,
    build_category_path,
)


def test_six_stage_category_path_and_missing_states() -> None:
    table = pa.table(
        {
            CATEGORY_COLUMNS[0]: [r"A\B"],
            CATEGORY_COLUMNS[1]: ["X > Y"],
            CATEGORY_COLUMNS[2]: [""],
            CATEGORY_COLUMNS[3]: [None],
            CATEGORY_COLUMNS[4]: ["E"],
            CATEGORY_COLUMNS[5]: ["F"],
        }
    )
    path = build_category_path(table)
    assert path.to_pylist() == [r"A\\B > X\ > Y >  > \N > E > F"]


def test_append_category_path_preserves_source_labels() -> None:
    values = {column: [f"value-{index}"] for index, column in enumerate(CATEGORY_COLUMNS)}
    table = pa.table(values)
    result = append_category_path(table)
    assert result.column_names == [*CATEGORY_COLUMNS, CATEGORY_PATH_COLUMN]
    for column in CATEGORY_COLUMNS:
        assert result[column].equals(table[column])
