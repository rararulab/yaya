from __future__ import annotations

import json

from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from yaya import __version__
from yaya.core import updater


def _releases_url() -> str:
    return updater.RELEASES_API


def test_update_check_up_to_date_json(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": __version__})
    result = runner.invoke(cli_app, ["--json", "update", "--check"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "update.check"
    assert payload["up_to_date"] is True
    assert payload["latest_version"] == __version__


def test_update_check_available_text(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "999.0.0"})
    result = runner.invoke(cli_app, ["update", "--check"])
    assert result.exit_code == 0
    assert "999.0.0" in result.stdout
    assert "Update available" in result.stdout


def test_update_check_available_json(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "999.0.0"})
    result = runner.invoke(cli_app, ["--json", "update", "--check"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "update.check"
    assert payload["up_to_date"] is False
    assert payload["latest_version"] == "999.0.0"
    assert "suggestion" in payload
    assert "yaya update" in payload["suggestion"]


def test_update_api_failure_shape(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), status_code=500)
    result = runner.invoke(cli_app, ["--json", "update", "--check"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["suggestion"]


def test_update_skip_persists_version(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "1.2.3"})
    result = runner.invoke(cli_app, ["--json", "update", "--skip"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "update.skip"
    assert payload["skipped_version"] == "1.2.3"
    assert updater.is_skipped("1.2.3")


def test_update_not_frozen_suggests_package_manager(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "999.0.0"})
    result = runner.invoke(cli_app, ["--json", "update"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["install_method"] == "pip-or-uv"
    assert "uv tool upgrade" in payload["suggestion"] or "pip install" in payload["suggestion"]


def test_update_help_has_example(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["update", "--help"])
    assert result.exit_code == 0
    assert "Examples" in result.stdout
    assert "yaya update --check" in result.stdout
