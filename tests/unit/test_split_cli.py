from __future__ import annotations

import pytest

from scene.cli import main


def test_split_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["split", "assign", "--help"])
    assert exc.value.code == 0
    assert "--config" in capsys.readouterr().out
