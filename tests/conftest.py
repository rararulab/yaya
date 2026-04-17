from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yaya.cli import app


@pytest.fixture
def runner() -> CliRunner:
    # Click >= 8.2 always captures stdout and stderr separately.
    return CliRunner()


@pytest.fixture
def cli_app():
    return app


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the updater state dir into tmp so tests do not touch ~/.local."""
    state = tmp_path / "yaya-state"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Re-bind the module-level paths since they were resolved at import time.
    from yaya.core import updater

    monkeypatch.setattr(updater, "STATE_DIR", state)
    monkeypatch.setattr(updater, "LATEST_VERSION_FILE", state / "latest_version.json")
    monkeypatch.setattr(updater, "SKIPPED_VERSION_FILE", state / "skipped_version.txt")
    return state


@pytest.fixture(autouse=True)
def _no_auto_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress the startup toast in CLI tests."""
    monkeypatch.setenv("YAYA_NO_AUTO_UPDATE", "1")


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


# Silence any accidental HTTP in unit tests by making the default base URL point
# to a sentinel that respx/pytest_httpx will fail on if unexpected.
os.environ.setdefault("NO_COLOR", "1")
