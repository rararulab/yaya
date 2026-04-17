from __future__ import annotations

import json

from typer.testing import CliRunner

from yaya import __version__


def test_version_text(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_json(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--json", "version"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {"version": __version__}


def test_root_version_flag(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_help_works(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "yaya" in result.stdout
    assert "update" in result.stdout


def test_no_args_prints_help(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout
    assert "update" in result.stdout
