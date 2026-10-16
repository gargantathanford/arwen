"""Verify wheel installs and rebuild the sdist without using editable imports.

Run from a locked development/build environment:
    python tools/check_distribution.py dist
Temporary installs are core-only, offline and isolated from PYTHONPATH.
"""

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

CORE_CHECK = r"""
import importlib
import importlib.metadata
import importlib.resources
import importlib.util
import pathlib
import pkgutil
import socket
import threading

assert importlib.util.find_spec("pymavlink") is None, "core install contains pymavlink"

def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Import attempted socket or worker startup")

socket.socket = forbidden
threading.Thread.start = forbidden
import arwen
for package in ("applications", "communication", "config", "control", "logging",
                "missions", "perception", "safety", "vehicle"):
    importlib.import_module("arwen." + package)
modules = [item.name for item in pkgutil.walk_packages(arwen.__path__, "arwen.")]
for name in modules:
    importlib.import_module(name)
assert "arwen.applications.mission_demo" in modules
assert "arwen.applications.telemetry" in modules
assert "site-packages" in str(pathlib.Path(arwen.__file__).resolve())
assert importlib.resources.files("arwen").joinpath("py.typed").is_file()
metadata = importlib.metadata.metadata("arwen-autonomy")
assert metadata["Requires-Python"] == ">=3.12"
for requirement in metadata.get_all("Requires-Dist", []):
    assert "extra ==" in requirement, "unexpected mandatory runtime dependency"
from arwen.communication.mavlink import Codec, MissingMavlink
try:
    Codec()
except MissingMavlink as exc:
    assert "[mavlink]" in str(exc)
else:
    raise AssertionError("missing optional dependency was not diagnosed")
print(f"Core wheel: {len(modules)} modules imported, typing metadata and optional isolation verified")
"""


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def check_wheel(wheel: Path, scratch: Path) -> None:
    environment = scratch / "venv"
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "Install the documented uv bootstrap tool before distribution checks"
        )
    run([uv, "venv", "--clear", "--python", sys.executable, str(environment)], scratch)
    executable = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(executable),
            "--offline",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        scratch,
    )
    run([str(executable), "-I", "-c", CORE_CHECK], scratch)
    for module, expected in (
        ("arwen.applications.authority_demo", "operator_takeover"),
        ("arwen.applications.execution_demo", "submitted\nacknowledged\ncompleted"),
        (
            "arwen.applications.mission_demo",
            "executing\nsucceeded\nexecution_completed",
        ),
    ):
        result = subprocess.run(
            [str(executable), "-I", "-m", module],
            cwd=scratch,
            text=True,
            capture_output=True,
            check=True,
        )
        if expected not in result.stdout:
            raise RuntimeError(
                f"Unexpected application behavior for {module}: {result.stdout}"
            )
        print(f"Installed application passed: {module}")


def only_artifact(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {directory}; found {len(matches)}")
    return matches[0].resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="directory with one fresh wheel and sdist"
    )
    args = parser.parse_args()
    wheel = only_artifact(args.directory, "*.whl")
    sdist = only_artifact(args.directory, "*.tar.gz")
    with tempfile.TemporaryDirectory(prefix="arwen-distribution-") as temporary:
        scratch = Path(temporary)
        check_wheel(wheel, scratch)
        source = scratch / "source"
        source.mkdir()
        with tarfile.open(sdist) as archive:
            archive.extractall(source, filter="data")
        roots = [path for path in source.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise ValueError("sdist must contain exactly one project directory")
        for required in (
            "uv.lock",
            "tools/check_distribution.py",
            "tests/test_missions.py",
        ):
            if not (roots[0] / required).is_file():
                raise ValueError(f"sdist missing development input: {required}")
        rebuilt = scratch / "rebuilt"
        run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--wheel",
                "--outdir",
                str(rebuilt),
                str(roots[0]),
            ],
            scratch,
        )
        check_wheel(only_artifact(rebuilt, "*.whl"), scratch)
    print("Original wheel and sdist-rebuilt wheel passed all distribution checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
