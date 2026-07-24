from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_m1_7_contracts_are_consistent(project_root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts/validate_m1_7_contracts.py"),
            "--project-root",
            str(project_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["error_count"] == 0
    assert payload["approved_decisions"] == [
        "D-018",
        "D-019",
        "D-020",
        "D-021",
        "D-022",
    ]
