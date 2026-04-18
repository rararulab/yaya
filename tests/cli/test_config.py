"""Tests for ``yaya config show`` CLI surface.

AC-bindings from ``specs/kernel-config.spec``:

* AC-02 redaction → ``test_json_redacts_openai_api_key`` and the
  variant-coverage parametrised case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yaya.cli.commands.config import _is_secret_key, redact
from yaya.kernel import config as cfg_mod


@pytest.fixture(autouse=True)
def _isolate_yaya_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip ``YAYA_*`` and point CONFIG_PATH at an empty tmp file."""
    import os

    for key in list(os.environ):
        if key.startswith("YAYA_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")


# `cli_app` is a fixture returning a `typer.Typer` app; pytest fixture
# resolution doesn't carry the type through here. Tests are out of scope
# for `[tool.mypy] files = ["src"]`, but the per-arg ignore documents
# intent if a future PR widens the mypy file set.
def test_config_show_text_mode(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(cli_app, ["config", "show"])
    assert result.exit_code == 0, result.stdout
    # The rich table renders these column headers / kernel field names.
    assert "bind_host" in result.stdout
    assert "127.0.0.1" in result.stdout
    assert "port" in result.stdout


def test_config_show_json_shape(runner: CliRunner, cli_app) -> None:  # type: ignore[no-untyped-def]  # see fixture note above
    result = runner.invoke(cli_app, ["--json", "config", "show"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "config.show"
    config = payload["config"]
    assert config["bind_host"] == "127.0.0.1"
    assert config["port"] == 0
    assert config["log_level"] == "INFO"


def test_json_redacts_openai_api_key(
    runner: CliRunner,
    cli_app,  # type: ignore[no-untyped-def]  # see fixture note above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-02: env-supplied secret never appears in stdout under --json."""
    monkeypatch.setenv("YAYA_LLM_OPENAI__API_KEY", "sk-abc123")
    result = runner.invoke(cli_app, ["--json", "config", "show"])
    assert result.exit_code == 0, result.stdout
    assert "sk-abc123" not in result.stdout

    payload = json.loads(result.stdout)
    assert payload["config"]["llm_openai"]["api_key"] == "***"


def test_text_mode_redacts_secrets(
    runner: CliRunner,
    cli_app,  # type: ignore[no-untyped-def]  # see fixture note above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redaction also applies in human-readable rendering."""
    monkeypatch.setenv("YAYA_LLM_OPENAI__API_KEY", "sk-abc123")
    result = runner.invoke(cli_app, ["config", "show"])
    assert result.exit_code == 0, result.stdout
    assert "sk-abc123" not in result.stdout
    assert "***" in result.stdout


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "API-KEY",
        "x_token",
        "secret_passphrase",
        "SECRET_PASSPHRASE",
        "openai_api_key",
        "github_token",
        "user_password",
        "PASSWORD",
    ],
)
def test_secret_regex_catches_variants(key: str) -> None:
    assert _is_secret_key(key), f"expected {key!r} to be flagged as secret"


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "host",
        "port",
        "log_level",
        "timeout_s",
        "endpoint",
    ],
)
def test_non_secret_keys_pass_through(key: str) -> None:
    """Plain config keys must NOT be redacted.

    Note: the regex is intentionally over-broad (substring match), so
    rare false-positives like ``monkey`` (matches ``key``) get redacted —
    that's a deliberate "fail-safe" trade.
    """
    assert not _is_secret_key(key), f"did not expect {key!r} to be flagged"


def test_redact_walks_nested_structures() -> None:
    raw = {
        "llm": {
            "model": "gpt-4o",
            "api_key": "sk-leak",
            "nested": {"secret": "x", "ok": "y"},
        },
        "list": [{"token": "leak"}, {"name": "ok"}],
    }
    out = redact(raw)
    redacted = "***"
    assert out["llm"]["model"] == "gpt-4o"
    assert out["llm"]["api_key"] == redacted
    assert out["llm"]["nested"]["secret"] == redacted
    assert out["llm"]["nested"]["ok"] == "y"
    assert out["list"][0]["token"] == redacted
    assert out["list"][1]["name"] == "ok"


def test_redact_does_not_mutate_input() -> None:
    raw = {"api_key": "sk-leak"}
    _ = redact(raw)
    assert raw["api_key"] == "sk-leak"
