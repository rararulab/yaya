from __future__ import annotations

import json

from typer.testing import CliRunner


def test_hello_default(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["hello"])
    assert result.exit_code == 0
    assert "world" in result.stdout


def test_hello_named(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["hello", "--name", "yaya"])
    assert result.exit_code == 0
    assert "yaya" in result.stdout


def test_hello_json_shape(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--json", "hello", "-n", "yaya"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "hello"
    assert payload["name"] == "yaya"
    assert payload["greeting"] == "Hello, yaya!"


def test_hello_help_includes_example(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["hello", "--help"])
    assert result.exit_code == 0
    assert "Examples" in result.stdout
    assert "yaya hello" in result.stdout
