"""Tests for maybe_show_update_toast."""

from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from yaya.cli import CLIState
from yaya.cli.commands.update import maybe_show_update_toast
from yaya.core import updater

pytestmark = pytest.mark.unit


def _force_tty(monkeypatch: MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: value)


def test_toast_suppressed_when_json(monkeypatch: MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    _force_tty(monkeypatch, True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 1}', encoding="utf-8")

    maybe_show_update_toast(CLIState(json_output=True))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_toast_suppressed_when_env_set(monkeypatch: MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("YAYA_NO_AUTO_UPDATE", "1")
    _force_tty(monkeypatch, True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 1}', encoding="utf-8")

    maybe_show_update_toast(CLIState())
    assert capsys.readouterr().err == ""


def test_toast_suppressed_when_not_tty(monkeypatch: MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    _force_tty(monkeypatch, False)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 1}', encoding="utf-8")

    maybe_show_update_toast(CLIState())
    assert capsys.readouterr().err == ""


def test_toast_shown_when_newer_cached(monkeypatch: MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    _force_tty(monkeypatch, True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 9999999999}', encoding="utf-8")

    maybe_show_update_toast(CLIState())
    err = capsys.readouterr().err
    assert "999.0.0" in err


def test_toast_skipped_when_version_in_skip_file(monkeypatch: MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    _force_tty(monkeypatch, True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 9999999999}', encoding="utf-8")
    updater.SKIPPED_VERSION_FILE.write_text("999.0.0", encoding="utf-8")

    maybe_show_update_toast(CLIState())
    assert capsys.readouterr().err == ""
