"""Tests for the emit_ok / emit_error / fatal output helpers."""

from __future__ import annotations

import json

import pytest

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok, fatal, warn

pytestmark = pytest.mark.unit


def test_emit_ok_json(capsys) -> None:
    emit_ok(CLIState(json_output=True), text="ignored", action="test", value=1)
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload == {"ok": True, "action": "test", "value": 1}
    assert out.err == ""


def test_emit_ok_text(capsys) -> None:
    emit_ok(CLIState(json_output=False), text="[bold]hi[/]", action="test")
    out = capsys.readouterr()
    assert "hi" in out.out


def test_emit_error_json_shape(capsys) -> None:
    emit_error(
        CLIState(json_output=True),
        error="boom",
        suggestion="retry",
        hint="x",
    )
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["ok"] is False
    assert payload["error"] == "boom"
    assert payload["suggestion"] == "retry"
    assert payload["hint"] == "x"


def test_emit_error_text_goes_to_stderr(capsys) -> None:
    emit_error(
        CLIState(json_output=False),
        error="boom",
        suggestion="retry",
    )
    out = capsys.readouterr()
    assert out.out == ""
    assert "boom" in out.err
    assert "retry" in out.err


def test_warn_goes_to_stderr(capsys) -> None:
    warn("careful")
    out = capsys.readouterr()
    assert out.out == ""
    assert "careful" in out.err


def test_fatal_exits_nonzero_with_json(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        fatal(CLIState(json_output=True), error="dead", suggestion="fix it")
    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["suggestion"] == "fix it"
