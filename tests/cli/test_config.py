"""Tests for the ``yaya config`` CLI surface (get / set / unset / list).

AC-bindings from ``specs/kernel-config-store.spec`` cover the CLI
commands (AC-07 / AC-08 / AC-09). The store itself is covered by
``tests/kernel/test_config_store.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_config_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect ``config.db`` under ``tmp_path`` via ``YAYA_STATE_DIR``.

    Without this, every test would land the CLI writes in the real
    ``~/.local/state/yaya/config.db`` — the exact data-loss bug the
    autouse state-dir fixture protects other tests from.
    """
    monkeypatch.setenv("YAYA_STATE_DIR", str(tmp_path / "state"))


def test_config_set_get_roundtrip(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """AC-07: ``set`` followed by ``get`` returns the stored value."""
    set_result = runner.invoke(cli_app, ["config", "set", "provider", "openai"])
    assert set_result.exit_code == 0, set_result.stdout + set_result.stderr
    get_result = runner.invoke(cli_app, ["config", "get", "provider"])
    assert get_result.exit_code == 0, get_result.stdout + get_result.stderr
    assert "openai" in get_result.stdout


def test_config_get_missing_key_exits_one(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(cli_app, ["config", "get", "nope"])
    assert result.exit_code == 1


def test_config_set_json_value(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """A JSON-shaped ``value`` is stored as a typed primitive."""
    assert runner.invoke(cli_app, ["config", "set", "session.default_id", '"demo"']).exit_code == 0
    result = runner.invoke(cli_app, ["--json", "config", "get", "session.default_id"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["value"] == "demo"


def test_config_unset_idempotent(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """AC-09: unsetting twice returns `removed=True` once, `False` next."""
    assert runner.invoke(cli_app, ["config", "set", "k", "v"]).exit_code == 0
    first = runner.invoke(cli_app, ["--json", "config", "unset", "k"])
    assert first.exit_code == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["removed"] is True
    second = runner.invoke(cli_app, ["--json", "config", "unset", "k"])
    assert second.exit_code == 0
    assert json.loads(second.stdout)["removed"] is False


def test_config_list_prefix(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """AC-08: ``list <prefix>`` returns only matching keys."""
    runner.invoke(cli_app, ["config", "set", "plugin.a.x", "1"])
    runner.invoke(cli_app, ["config", "set", "plugin.a.y", "2"])
    runner.invoke(cli_app, ["config", "set", "plugin.b.x", "3"])
    result = runner.invoke(cli_app, ["--json", "config", "list", "plugin.a."])
    assert result.exit_code == 0, result.stdout + result.stderr
    entries = json.loads(result.stdout)["entries"]
    assert set(entries.keys()) == {"plugin.a.x", "plugin.a.y"}


def test_config_list_masks_secrets_by_default(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """AC-10: ``list -v`` masks secret-suffix keys unless ``--show-secrets``."""
    runner.invoke(cli_app, ["config", "set", "plugin.llm_openai.api_key", '"sk-abcdef1234"'])
    masked = runner.invoke(cli_app, ["--json", "config", "list", "plugin.llm_openai.", "-v"])
    entries = json.loads(masked.stdout)["entries"]
    assert entries["plugin.llm_openai.api_key"] == "****1234"

    revealed = runner.invoke(
        cli_app,
        ["--json", "config", "list", "plugin.llm_openai.", "-v", "--show-secrets"],
    )
    entries_revealed = json.loads(revealed.stdout)["entries"]
    assert entries_revealed["plugin.llm_openai.api_key"] == "sk-abcdef1234"


def test_config_list_keys_only(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Without ``-v`` the list is keys only, no values."""
    runner.invoke(cli_app, ["config", "set", "k", "v"])
    result = runner.invoke(cli_app, ["config", "list"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "k" in result.stdout
    assert '"v"' not in result.stdout


def test_is_secret_key() -> None:
    """The CLI-level secret predicate matches dotted suffixes exactly."""
    from yaya.cli.commands.config import _is_secret_key

    assert _is_secret_key("plugin.llm_openai.api_key")
    assert _is_secret_key("api_key")
    assert _is_secret_key("plugin.x.token")
    assert _is_secret_key("plugin.y.secret")
    assert _is_secret_key("plugin.z.password")
    assert not _is_secret_key("plugin.x.model")
    # Substring match must NOT trigger — ``apikeys`` is not ``api_key``.
    assert not _is_secret_key("plugin.x.apikeys")


def test_mask_secret_short_and_long() -> None:
    """Short secrets collapse fully; longer ones reveal last-4 chars."""
    from yaya.cli.commands.config import _mask_secret

    assert _mask_secret("abcd") == "****"
    assert _mask_secret("abcdef") == "****cdef"
    assert _mask_secret(12345) == "****"


def test_config_set_list_human_output(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Human-mode ``list -v`` also masks secrets without ``--show-secrets``."""
    runner.invoke(cli_app, ["config", "set", "plugin.llm_openai.api_key", '"sk-abcdef1234"'])
    result = runner.invoke(cli_app, ["config", "list", "-v"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "sk-abcdef" not in result.stdout
    assert "****" in result.stdout


def test_config_unset_human_output(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Human-mode ``unset`` prints a terminal-friendly line."""
    runner.invoke(cli_app, ["config", "set", "k", "v"])
    result = runner.invoke(cli_app, ["config", "unset", "k"])
    assert result.exit_code == 0
    assert "k" in result.stdout


def test_config_set_not_json_falls_back_to_string(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Bare ``openai`` parses as raw string, not rejected."""
    result = runner.invoke(cli_app, ["config", "set", "p", "openai"])
    assert result.exit_code == 0
    get_result = runner.invoke(cli_app, ["--json", "config", "get", "p"])
    assert json.loads(get_result.stdout)["value"] == "openai"


def test_config_list_empty_store(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Empty store produces a ``(no keys)`` line, not a crash."""
    result = runner.invoke(cli_app, ["config", "list", "nonexistent."])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "no keys" in result.stdout


def test_config_set_missing_value_errors(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """Typer flags the missing required positional argument with exit 2."""
    result = runner.invoke(cli_app, ["config", "set", "only-key"])
    assert result.exit_code != 0


def test_config_get_json_missing_key(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    """JSON mode still prints a structured error body on missing key."""
    result = runner.invoke(cli_app, ["--json", "config", "get", "absent"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "key_not_found"
