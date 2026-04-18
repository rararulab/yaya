"""Output helpers enforcing rararulab agent-friendly-cli conventions.

See: docs/agent-friendly-cli.md

Contract:
- JSON mode (``--json``): stdout carries a single JSON object following the
  canonical ``{"ok": bool, ...}`` shape. Human-facing logs go to stderr.
- Text mode (default): rich output on stdout for humans; progress/warnings
  on stderr.

Errors always include an ``error`` and ``suggestion`` field so agents can
self-correct without reading prose.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console

from yaya.cli import CLIState

_stdout = Console()
_stderr = Console(stderr=True)


def emit_ok(state: CLIState, *, action: str, text: str | None = None, **data: Any) -> None:
    """Emit a success result.

    Agent mode: ``{"ok": true, "action": action, ...data}`` on stdout.
    Human mode: ``text`` (rich markup) on stdout; when ``text`` is
    ``None`` or empty, human mode prints nothing — useful for commands
    (``plugin list``) that render their own human output separately.
    """
    if state.json_output:
        payload: dict[str, Any] = {"ok": True, "action": action, **data}
        _stdout.print_json(json.dumps(payload))
    elif text:
        _stdout.print(text)


def emit_error(
    state: CLIState,
    *,
    error: str,
    suggestion: str = "",
    text: str | None = None,
    **data: Any,
) -> None:
    """Emit an error result. Caller is responsible for the non-zero exit.

    Agent mode: ``{"ok": false, "error": ..., "suggestion": ..., ...data}``
    on stdout (stdout so a single pipe gets the full response).
    Human mode: ``text`` (or a default rendering) on stderr.
    """
    if state.json_output:
        payload: dict[str, Any] = {
            "ok": False,
            "error": error,
            "suggestion": suggestion,
            **data,
        }
        _stdout.print_json(json.dumps(payload))
    else:
        rendered = text
        if rendered is None:
            rendered = f"[red]Error:[/] {error}"
            if suggestion:
                rendered += f"\n[dim]Suggestion:[/] {suggestion}"
        _stderr.print(rendered)


def warn(message: str) -> None:
    """Human-facing warning — always stderr, suppressed under --json."""
    _stderr.print(message)


def fatal(state: CLIState, *, error: str, suggestion: str = "", code: int = 1) -> None:
    """Emit an error and exit with a non-zero code."""
    emit_error(state, error=error, suggestion=suggestion)
    sys.exit(code)
