"""CLI tests for ``yaya session {list,show,resume,archive}``."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from yaya.kernel import SessionStore


@pytest.fixture(autouse=True)
def _isolate_session_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session paths at a per-test tmp dir; never touch real XDG state."""
    monkeypatch.setenv("YAYA_STATE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_session(workspace: Path, session_id: str, tapes_dir: Path) -> None:
    """Create a file-backed session with a couple of messages so CLI has data."""

    async def _run() -> None:
        store = SessionStore(tapes_dir=tapes_dir)
        try:
            session = await store.open(workspace, session_id)
            await session.append_message("user", "hello")
            await session.append_message("assistant", "hi there")
        finally:
            await store.close()

    asyncio.run(_run())


def test_session_list_json_empty(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    result = runner.invoke(cli_app, ["--json", "session", "list"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "session.list"
    assert payload["sessions"] == []


def test_session_list_json_populated(
    runner: CliRunner,
    cli_app: Any,
    tmp_path: Path,
) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(cli_app, ["--json", "session", "list"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["entry_count"] >= 2


def test_session_list_text_table(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(cli_app, ["session", "list"])
    assert result.exit_code == 0, result.stdout
    assert "yaya sessions" in result.stdout


def test_session_show_json_tail(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(cli_app, ["--json", "session", "show", "default", "--tail", "1"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "session.show"
    assert len(payload["entries"]) == 1


def test_session_resume_missing_returns_error(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    result = runner.invoke(cli_app, ["--json", "session", "resume", "nope"])
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "not found" in payload["error"]


def test_session_resume_existing(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(cli_app, ["--json", "session", "resume", "default"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["session_id"] == "default"


def test_session_archive_requires_yes_under_json(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(cli_app, ["--json", "session", "archive", "default"])
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "confirmation_required"


def test_session_archive_happy_path(runner: CliRunner, cli_app: Any, tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    _seed_session(tmp_path, "default", tapes_dir)
    result = runner.invoke(
        cli_app,
        ["--json", "session", "archive", "default", "--yes"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "session.archive"
    archive_path = Path(payload["archive_path"])
    assert archive_path.exists()
