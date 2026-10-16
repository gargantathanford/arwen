"""Architecture failures must be rejected by the standalone validation command."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "owner,code,expected",
    [
        ("control", "from arwen.vehicle.contracts import VehicleState", None),
        (
            "missions",
            "from arwen.control.execution import ExecutionController",
            "missions must not import control",
        ),
        (
            "safety",
            "from arwen import communication",
            "safety must not import communication",
        ),
        ("vehicle", "import pymavlink", "pymavlink belongs inside communication"),
        (
            "control",
            "from .authority import AuthorityController",
            "use absolute package imports",
        ),
        (
            "missions",
            "importlib.import_module('arwen.communication.px4')",
            "missions must not import communication",
        ),
    ],
)
def test_documented_boundaries(
    tmp_path: Path, owner: str, code: str, expected: str | None
) -> None:
    directory = tmp_path / owner
    directory.mkdir()
    (directory / "example.py").write_text(code)
    script = Path(__file__).resolve().parents[1] / "tools" / "check_imports.py"
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if expected is None:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1
        assert expected in result.stdout
