from __future__ import annotations

import json
from unittest.mock import patch

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
