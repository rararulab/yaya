"""Output helpers — dual mode (rich text vs JSON)."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console

from yaya.cli import CLIState

_stdout = Console()
_stderr = Console(stderr=True)


def emit(state: CLIState, *, text: str, data: dict[str, Any]) -> None:
    """Emit a result: text to stdout (rich) or JSON (if --json)."""
    if state.json_output:
        _stdout.print_json(json.dumps(data))
    else:
        _stdout.print(text)


def warn(message: str) -> None:
    """Human-facing warning — always goes to stderr."""
    _stderr.print(message)


def fatal(message: str, code: int = 1) -> None:
    _stderr.print(message)
    sys.exit(code)
