"""Snapshot tests for --help outputs.

When help text changes intentionally (new command, new flag), refresh
snapshots with ``pytest --snapshot-update``. Snapshots live under
``tests/cli/__snapshots__/`` and are checked into git.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.unit

_BOX_CORNER_TRANSLATION = str.maketrans(
    {
        "┌": "╭",
        "┐": "╮",
        "└": "╰",
        "┘": "╯",
    },
)


def _normalize(output: str) -> str:
    # Strip trailing whitespace per line and collapse box-drawing artifacts
    # so minor width/terminal differences do not fail the snapshot.
    return "\n".join(line.rstrip().translate(_BOX_CORNER_TRANSLATION) for line in output.strip().splitlines())


def test_root_help_snapshot(runner: CliRunner, cli_app, snapshot) -> None:
    result = runner.invoke(cli_app, ["--help"], terminal_width=100)
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot


def test_update_help_snapshot(runner: CliRunner, cli_app, snapshot) -> None:
    result = runner.invoke(cli_app, ["update", "--help"], terminal_width=100)
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot
