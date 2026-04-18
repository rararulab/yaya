"""Post-install smoke tests for the yaya CLI.

Minimum bar: after `pip install dist/*.whl` the user gets a working
CLI with stable JSON contracts. These tests run against whatever
`yaya` is on PATH — the installed wheel, the installed sdist, or a
PyInstaller binary when `YAYA_BIN` points at one.
"""

from __future__ import annotations

import pytest

from .conftest import json_stdout, run

pytestmark = [pytest.mark.integration]


def test_version_exits_zero(yaya_bin: str) -> None:
    result = run(yaya_bin, "version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()  # non-empty


def test_root_version_flag(yaya_bin: str) -> None:
    result = run(yaya_bin, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_version_json_shape(yaya_bin: str) -> None:
    payload = json_stdout(run(yaya_bin, "--json", "version"))
    assert payload["ok"] is True
    assert payload["action"] == "version"
    assert isinstance(payload.get("version"), str)
    assert payload["version"]


def test_hello_kernel_smoke(yaya_bin: str) -> None:
    """``yaya hello`` round-trips one event through the kernel."""
    result = run(yaya_bin, "hello")
    assert result.returncode == 0, result.stderr
    assert "kernel ok" in result.stdout


def test_hello_json_shape(yaya_bin: str) -> None:
    payload = json_stdout(run(yaya_bin, "--json", "hello"))
    assert payload["ok"] is True
    assert payload["action"] == "hello"
    assert payload["received"] is True
    assert isinstance(payload.get("version"), str)


def test_help_lists_every_known_subcommand(yaya_bin: str) -> None:
    result = run(yaya_bin, "--help")
    assert result.returncode == 0, result.stderr
    # Every subcommand currently registered in src/yaya/cli/__init__.py
    for cmd in ("hello", "version", "update", "serve", "plugin"):
        assert cmd in result.stdout, f"{cmd} missing from --help"


def test_no_args_shows_help_and_exits_zero(yaya_bin: str) -> None:
    result = run(yaya_bin)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


def test_unknown_command_fails_with_nonzero(yaya_bin: str) -> None:
    result = run(yaya_bin, "this-command-does-not-exist")
    assert result.returncode != 0
