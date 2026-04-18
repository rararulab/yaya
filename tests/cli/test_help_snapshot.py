"""Snapshot tests for CLI help output.

Rich renders box-drawing glyphs that differ across platforms (Windows
terminals often substitute doubled/heavy variants or ASCII fallbacks).
To keep the snapshots deterministic on ubuntu / macos / windows, every
box-drawing codepoint is collapsed to a canonical ASCII form before the
string is handed to syrupy.
"""

from __future__ import annotations

import re

from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

# Rich box-drawing glyphs → ASCII canonical form. Covers the single /
# doubled / heavy variants rich may pick depending on terminal locale
# and Windows codepage. The mapping is intentionally lossy: we only care
# that the *structure* of the help panel is stable, not the exact glyph.
_BOX_TO_ASCII = str.maketrans(
    {
        # Single light box
        "\u256d": "+",  # ╭
        "\u256e": "+",  # ╮
        "\u256f": "+",  # ╯
        "\u2570": "+",  # ╰
        "\u2500": "-",  # ─
        "\u2502": "|",  # │
        "\u251c": "+",  # ├
        "\u2524": "+",  # ┤
        "\u252c": "+",  # ┬
        "\u2534": "+",  # ┴
        "\u253c": "+",  # ┼
        # Doubled box
        "\u2550": "-",  # ═
        "\u2551": "|",  # ║
        "\u2554": "+",  # ╔
        "\u2557": "+",  # ╗
        "\u255a": "+",  # ╚
        "\u255d": "+",  # ╝
        "\u2560": "+",  # ╠
        "\u2563": "+",  # ╣
        "\u2566": "+",  # ╦
        "\u2569": "+",  # ╩
        "\u256c": "+",  # ╬
        # Heavy box
        "\u2501": "-",  # ━
        "\u2503": "|",  # ┃
        "\u250f": "+",  # ┏
        "\u2513": "+",  # ┓
        "\u2517": "+",  # ┗
        "\u251b": "+",  # ┛
        "\u2523": "+",  # ┣
        "\u252b": "+",  # ┫
        "\u2533": "+",  # ┳
        "\u253b": "+",  # ┻
        "\u254b": "+",  # ╋
    }
)

# ANSI CSI escape sequences. Rich may emit these even under NO_COLOR in
# some terminals; strip defensively.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _normalize(text: str) -> str:
    """Normalize rich-rendered text so snapshots are platform-stable.

    - Strip ANSI escape sequences.
    - Substitute every box-drawing codepoint with an ASCII stand-in.
    - Trim trailing whitespace per line (terminals pad to width
      differently on Windows).
    """
    text = _ANSI_RE.sub("", text)
    text = text.translate(_BOX_TO_ASCII)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).rstrip() + "\n"


def test_root_help_snapshot(
    runner: CliRunner, cli_app, snapshot: SnapshotAssertion
) -> None:
    """Top-level `yaya --help` renders a stable panel layout."""
    result = runner.invoke(cli_app, ["--help"], env={"COLUMNS": "100"})
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot


def test_update_help_snapshot(
    runner: CliRunner, cli_app, snapshot: SnapshotAssertion
) -> None:
    """`yaya update --help` renders a stable panel layout."""
    result = runner.invoke(cli_app, ["update", "--help"], env={"COLUMNS": "100"})
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot
