from __future__ import annotations

import json

from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from yaya import __version__
from yaya.core import updater


def _releases_url() -> str:
    return updater.RELEASES_API


def test_update_check_up_to_date(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": __version__})
    result = runner.invoke(cli_app, ["update", "--check"])
    assert result.exit_code == 0
    assert "up to date" in result.stdout.lower()


def test_update_check_available(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
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
    assert payload["result"] == "UPDATE_AVAILABLE"
    assert payload["latest_version"] == "999.0.0"


def test_update_api_failure(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), status_code=500)
    result = runner.invoke(cli_app, ["update", "--check"])
    assert result.exit_code == 1


def test_update_skip_persists_version(runner: CliRunner, cli_app, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "1.2.3"})
    result = runner.invoke(cli_app, ["update", "--skip"])
    assert result.exit_code == 0
    assert "1.2.3" in result.stdout
    assert updater.is_skipped("1.2.3")


def test_update_not_frozen_points_to_package_manager(
    runner: CliRunner, cli_app, httpx_mock: HTTPXMock, monkeypatch
) -> None:
    # is_frozen -> False in tests, so `update` (no --check) should bail out
    # with guidance rather than attempt to replace sys.executable.
    httpx_mock.add_response(url=_releases_url(), json={"tag_name": "999.0.0"})
    result = runner.invoke(cli_app, ["update"])
    assert result.exit_code == 0
    assert "pip install -U yaya" in result.stdout or "uv tool upgrade" in result.stdout
