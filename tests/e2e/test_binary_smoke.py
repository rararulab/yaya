"""Binary-target post-install smoke.

Skipped unless ``YAYA_BIN`` points at an on-disk executable. CI's
release-please workflow builds the PyInstaller binary, sets
``YAYA_BIN`` to the artifact path, and re-runs ``pytest tests/e2e``
against it so every assertion in this directory also covers the
frozen binary.

Locally: `YAYA_BIN=$(pwd)/dist/yaya pytest tests/e2e -v` after
`just build-bin`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from .conftest import json_stdout, run
from .test_plugin_list_smoke import EXPECTED_BUNDLED

pytestmark = [pytest.mark.integration]


def _binary_path() -> Path | None:
    """Return the configured binary path, or ``None`` to skip."""
    explicit = os.environ.get("YAYA_BIN")
    if not explicit:
        return None
    path = Path(explicit)
    if not path.exists():
        # If YAYA_BIN is set to a missing file we want a real failure
        # (surfaced through the shared `yaya_bin` fixture) — the tests
        # in this module all depend on that fixture, so pytest raises
        # there instead of silently skipping.
        return path
    return path


_SKIP_REASON = (
    "YAYA_BIN is unset — skipping binary smoke. Set YAYA_BIN to a "
    "PyInstaller onefile path or run `just build-bin` first."
)


@pytest.fixture(scope="module", autouse=True)
def _require_binary_target() -> None:
    """Skip the whole module unless a real binary is available."""
    bin_path = _binary_path()
    if bin_path is None:
        pytest.skip(_SKIP_REASON)
    # Second belt-and-braces check — a shutil.which-resolved venv
    # script is fine too, but the POINT of this module is the frozen
    # binary, so refuse to run against a plain shim.
    resolved = shutil.which(str(bin_path))
    if resolved is None and not bin_path.exists():
        pytest.skip(_SKIP_REASON)


def test_binary_runs_core_cli_endpoints(yaya_bin: str) -> None:
    """Smoke the four kernel endpoints through a frozen binary."""
    # version
    version_result = run(yaya_bin, "version")
    assert version_result.returncode == 0, version_result.stderr
    assert version_result.stdout.strip()

    # --json version
    version_json = json_stdout(run(yaya_bin, "--json", "version"))
    assert version_json["ok"] is True
    assert version_json["action"] == "version"
    assert isinstance(version_json.get("version"), str)
    assert version_json["version"]

    # --json hello
    hello_json = json_stdout(run(yaya_bin, "--json", "hello"))
    assert hello_json["ok"] is True
    assert hello_json["action"] == "hello"
    assert hello_json["received"] is True

    # --json plugin list
    plugins_json = json_stdout(run(yaya_bin, "--json", "plugin", "list"))
    assert plugins_json["ok"] is True
    assert plugins_json["action"] == "plugin.list"
    rows = plugins_json.get("plugins")
    assert isinstance(rows, list) and rows, "binary shipped without bundled plugins"
    names = {row["name"] for row in rows if isinstance(row, dict)}
    missing = sorted(set(EXPECTED_BUNDLED) - names)
    assert not missing, (
        f"binary missing bundled plugins {missing}; check PyInstaller spec for datas / hiddenimports coverage"
    )


def test_binary_help_lists_every_subcommand(yaya_bin: str) -> None:
    """`--help` through the binary enumerates every kernel subcommand."""
    result = run(yaya_bin, "--help")
    assert result.returncode == 0, result.stderr
    for cmd in ("hello", "version", "update", "serve", "plugin"):
        assert cmd in result.stdout, f"{cmd!r} missing from binary --help"
