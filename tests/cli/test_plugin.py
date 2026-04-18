from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner


def test_plugin_list_json(runner: CliRunner, cli_app) -> None:
    """``plugin list --json`` returns the seeded bundled plugins."""
    result = runner.invoke(cli_app, ["--json", "plugin", "list"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "plugin.list"
    names = {row["name"] for row in payload["plugins"]}
    # Four seed plugins ship in the repo — see pyproject.toml entry points.
    assert {"strategy-react", "memory-sqlite", "llm-openai", "tool-bash"}.issubset(names)


def test_plugin_list_text_renders_table(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["plugin", "list"])
    assert result.exit_code == 0
    assert "yaya plugins" in result.stdout
    assert "strategy-react" in result.stdout


def test_plugin_remove_bundled_rejected(runner: CliRunner, cli_app) -> None:
    """Removing a bundled plugin surfaces ok=false + a ``bundled`` suggestion."""
    result = runner.invoke(cli_app, ["--json", "plugin", "remove", "strategy-react", "--yes"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "bundled" in payload["error"]
    assert "bundled" in payload["suggestion"]


def test_plugin_install_shell_metachars_rejected(runner: CliRunner, cli_app) -> None:
    """Validator rejects unsupported sources *before* any pip subprocess.

    Shell-injection safety comes from ``_run_package_command`` using
    ``create_subprocess_exec`` (no shell) — not from character
    filtering. This test covers the surviving surface of
    ``validate_install_source``: unsupported URL schemes like
    ``git+ssh`` are rejected before any subprocess runs.
    """
    result = runner.invoke(
        cli_app,
        ["--json", "plugin", "install", "git+ssh://example.com/foo.git", "--yes"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "scheme" in payload["error"] or "supported" in payload["error"]


def test_plugin_install_json_requires_yes(runner: CliRunner, cli_app) -> None:
    """Under ``--json`` we refuse to prompt — must pass ``--yes``."""
    result = runner.invoke(cli_app, ["--json", "plugin", "install", "some-pkg"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "confirmation_required"


def test_plugin_install_dry_run(runner: CliRunner, cli_app) -> None:
    """--dry-run skips pip entirely."""
    with patch("yaya.kernel.registry._run_package_command", new_callable=AsyncMock) as fake_run:
        result = runner.invoke(
            cli_app,
            ["--json", "plugin", "install", "some-pkg", "--yes", "--dry-run"],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    fake_run.assert_not_called()


def test_plugin_install_invokes_pip(runner: CliRunner, cli_app) -> None:
    """Happy path: validator passes, subprocess is mocked, ok=true lands."""
    with patch("yaya.kernel.registry._run_package_command", new_callable=AsyncMock) as fake_run:
        result = runner.invoke(
            cli_app,
            ["--json", "plugin", "install", "some-pkg", "--yes"],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "plugin.install"
    fake_run.assert_awaited()
