from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


def test_hello_json_ok(runner: CliRunner, cli_app) -> None:
    """Happy path: bus + registry + loop boot and the sentinel fires."""
    result = runner.invoke(cli_app, ["--json", "hello"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "hello"
    assert payload["received"] is True


def test_hello_text_ok(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["hello"])
    assert result.exit_code == 0
    assert "kernel ok" in result.stdout


def test_hello_help_includes_example(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["hello", "--help"])
    assert result.exit_code == 0
    assert "Examples" in result.stdout


def test_hello_startup_failure(runner: CliRunner, cli_app) -> None:
    """When AgentLoop.start raises, the command surfaces ok=false, exit 1."""
    with patch("yaya.cli.commands.hello.AgentLoop") as fake_loop_cls:
        fake_loop_cls.return_value.start.side_effect = RuntimeError("boom")
        result = runner.invoke(cli_app, ["--json", "hello"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "kernel_startup_failed" in payload["error"]
    assert payload["suggestion"]


def test_hello_json_emits_single_object_without_toast(
    runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``yaya --json hello`` must not leak an update toast into stdout/stderr."""
    from yaya.core import updater

    # Prime the cached latest-version file with a higher version; without
    # the toast-exclusion for ``hello`` this would render on stderr.
    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 9999999999}', encoding="utf-8")

    result = runner.invoke(cli_app, ["--json", "hello"])
    assert result.exit_code == 0, result.stdout
    # stdout must parse as exactly one JSON object.
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "hello"
    # stderr must not contain the update-toast version string.
    assert "999.0.0" not in (result.stderr or "")


def test_hello_timeout_surfaces_unresponsive(runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the bus silently drops the synthetic event, hello exits 1."""
    from yaya.cli.commands import hello as hello_mod

    # Tight deadline so the test completes quickly even if publish stalls.
    monkeypatch.setattr(hello_mod, "_SENTINEL_TIMEOUT_S", 0.05)

    # Replace publish with a no-op so the sentinel never fires.
    async def _noop_publish(self, event):
        return None

    monkeypatch.setattr("yaya.kernel.bus.EventBus.publish", _noop_publish)

    result = runner.invoke(cli_app, ["--json", "hello"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "event_bus_unresponsive"
    assert payload["suggestion"]
