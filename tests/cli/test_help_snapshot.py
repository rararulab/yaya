"""Snapshot tests for CLI help output.

Rich renders box-drawing glyphs that differ across platforms (Windows
terminals often substitute doubled/heavy variants or ASCII fallbacks).
To keep the snapshots deterministic on ubuntu / macos / windows, every
box-drawing codepoint is collapsed to a canonical ASCII form and the
terminal width is pinned before the string is handed to syrupy.
"""

from __future__ import annotations

import re

from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

# Rich box-drawing glyphs -> ASCII canonical form. Covers the single /
# doubled / heavy variants rich may pick depending on terminal locale
# and Windows codepage. The mapping is intentionally lossy: we only care
# that the *structure* of the help panel is stable, not the exact glyph.
_BOX_TO_ASCII = str.maketrans({
    # Single light box
    "\u256d": "+",
    "\u256e": "+",
    "\u256f": "+",
    "\u2570": "+",
    "\u2500": "-",
    "\u2502": "|",
    "\u251c": "+",
    "\u2524": "+",
    "\u252c": "+",
    "\u2534": "+",
    "\u253c": "+",
    # Doubled box
    "\u2550": "-",
    "\u2551": "|",
    "\u2554": "+",
    "\u2557": "+",
    "\u255a": "+",
    "\u255d": "+",
    "\u2560": "+",
    "\u2563": "+",
    "\u2566": "+",
    "\u2569": "+",
    "\u256c": "+",
    # Heavy box
    "\u2501": "-",
    "\u2503": "|",
    "\u250f": "+",
    "\u2513": "+",
    "\u2517": "+",
    "\u251b": "+",
    "\u2523": "+",
    "\u252b": "+",
    "\u2533": "+",
    "\u253b": "+",
    "\u254b": "+",
})

# ANSI CSI escape sequences. Rich may emit these even under NO_COLOR in
# some terminals; strip defensively.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


_BORDER_RUN_RE = re.compile(r"-{3,}")
_SPACE_RUN_RE = re.compile(r"\s{2,}")
_PANEL_HEADER_RE = re.compile(r"^\+- (\S+) ---\+?$")


def _normalize(text: str) -> str:
    """Normalize rich-rendered text so snapshots are platform-stable.

    Rich's rendered width depends on the runner's effective `COLUMNS`,
    which differs between ubuntu, macos, and windows CI images. To be
    resilient to every width we collapse all width-dependent structure:

    - Strip ANSI escape sequences.
    - Substitute every box-drawing codepoint with an ASCII stand-in.
    - Collapse horizontal border runs (`---...`) to a fixed `---`.
    - Drop the leading/trailing `|` panel walls so content-column width
      no longer matters.
    - Rewrap wrapped content inside panels: consecutive non-header
      lines in the same panel are merged and re-split on whitespace,
      then re-emitted one semantic token per line when the first token
      looks like an option flag or command name.
    - Collapse all runs of whitespace (inside lines and across lines)
      to single spaces before comparing.
    """
    text = _ANSI_RE.sub("", text)
    text = text.translate(_BOX_TO_ASCII)
    stripped = [_strip_walls(raw) for raw in text.splitlines()]
    panels_collapsed = _collapse_panels(stripped)
    paragraphs = _collapse_paragraphs(panels_collapsed)
    return _collapse_blanks(paragraphs)


def _collapse_paragraphs(lines: list[str]) -> list[str]:
    """Join wrapped paragraph lines outside panels into single rows.

    Free-form text outside a panel (e.g. the trailing `Examples:` line)
    is wrapped by rich at the terminal width. We join consecutive
    non-blank, non-panel lines together so the rendered width no longer
    matters.
    """
    out: list[str] = []
    para: list[str] = []
    for line in lines:
        is_structural = line == "" or line.startswith(("+", "|"))
        if is_structural:
            if para:
                out.append(" ".join(para))
                para = []
            out.append(line)
        else:
            para.append(line.strip())
    if para:
        out.append(" ".join(para))
    return out


def _strip_walls(raw: str) -> str:
    """Strip leading/trailing `|` panel walls and collapse border runs."""
    line = _BORDER_RUN_RE.sub("---", raw.rstrip())
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return line.rstrip()


def _collapse_panels(lines: list[str]) -> list[str]:
    """Merge wrapped panel bodies into semantic rows."""
    out: list[str] = []
    buf: list[str] = []
    in_panel = False
    for line in lines:
        header = _PANEL_HEADER_RE.match(line.strip())
        if header:
            if buf:
                out.extend(_flush_panel(buf))
                buf = []
            out.append(f"+- {header.group(1)} ---+")
            in_panel = True
            continue
        if in_panel and line.strip().startswith("+"):
            if buf:
                out.extend(_flush_panel(buf))
                buf = []
            out.append("+---+")
            in_panel = False
            continue
        if in_panel:
            buf.append(line)
        else:
            out.append(_SPACE_RUN_RE.sub(" ", line).strip())
    if buf:
        out.extend(_flush_panel(buf))
    return out


def _collapse_blanks(lines: list[str]) -> str:
    """Collapse runs of blank lines to a single blank, normalize trailing."""
    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line == ""
        if blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = blank
    return "\n".join(collapsed).strip() + "\n"


def _flush_panel(lines: list[str]) -> list[str]:
    """Merge wrapped panel-body lines into semantic rows.

    Rich wraps long help text by starting continuation lines with
    leading spaces (no flag). We detect a "new row" by the presence of
    a leading token that is a flag (`--foo`, `-f`) or a bare word with
    a trailing 2+-space gap. Everything else is a continuation that
    gets appended to the previous row.
    """
    rows: list[str] = []
    current = ""
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        # Heuristic: a new row starts with either `--flag`, `-f`, or a
        # plain word immediately followed by 2+ spaces (column gap).
        is_new = False
        if stripped.startswith("-"):
            is_new = True
        else:
            head = raw.lstrip()
            # Commands panel: first token then 2+ spaces then text.
            m = re.match(r"^\S+\s{2,}\S", head)
            if m:
                is_new = True
        if is_new:
            if current:
                rows.append(_SPACE_RUN_RE.sub(" ", current).strip())
            current = stripped
        else:
            current = f"{current} {stripped}" if current else stripped
    if current:
        rows.append(_SPACE_RUN_RE.sub(" ", current).strip())
    return [f"| {row}" for row in rows]


def test_root_help_snapshot(
    runner: CliRunner,
    cli_app,
    snapshot: SnapshotAssertion,
) -> None:
    """Top-level `yaya --help` renders a stable panel layout."""
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot


def test_update_help_snapshot(
    runner: CliRunner,
    cli_app,
    snapshot: SnapshotAssertion,
) -> None:
    """`yaya update --help` renders a stable panel layout."""
    result = runner.invoke(cli_app, ["update", "--help"])
    assert result.exit_code == 0
    assert _normalize(result.stdout) == snapshot
