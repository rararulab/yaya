"""E2E test configuration.

These tests run against the installed `yaya` command (from a wheel or
binary), not against the source tree. They deliberately avoid the
unit-test `conftest.py` fixtures that monkey-patch the updater state
dir and suppress the update toast — we want this code path to look
like a real install.

All tests in this directory are marked `integration` so they are
skipped by the default unit-test run (`-m "not integration"` in CI
for the unit legs).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def yaya_bin() -> str:
    """Locate the installed `yaya` executable.

    Prefers `$YAYA_BIN` (set in CI to point at a specific binary), then
    falls back to `shutil.which("yaya")` for the wheel-install path.
    """
    explicit = os.environ.get("YAYA_BIN")
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"YAYA_BIN points at missing file: {path}")
        return str(path)
    found = shutil.which("yaya")
    if found is None:
        raise RuntimeError(
            "`yaya` is not on PATH. Install the wheel into the active "
            "venv (or set YAYA_BIN to a binary path) before running "
            "tests/e2e.",
        )
    return found


def run(yaya_bin: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke yaya and capture outputs; never raise on non-zero."""
    env = {**os.environ, "YAYA_NO_AUTO_UPDATE": "1", "NO_COLOR": "1"}
    return subprocess.run(
        [yaya_bin, *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Parse the JSON body from yaya's stdout, failing loudly on drift."""
    if result.returncode != 0:
        raise AssertionError(
            f"yaya exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout is not JSON: {exc}\nraw stdout:\n{result.stdout}",
        ) from exc
    if not isinstance(data, dict):
        raise AssertionError(f"expected object, got {type(data).__name__}")  # noqa: TRY004
    return data
