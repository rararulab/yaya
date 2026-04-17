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


def test_hello_json(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--json", "hello", "-n", "yaya"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {"greeting": "Hello, yaya!", "name": "yaya"}
