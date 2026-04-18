"""``yaya config show`` — print the resolved kernel configuration.

Read-only diagnostic. Prints the merged result of CLI flags (none for
this command) → ``YAYA_*`` env vars → ``$XDG_CONFIG_HOME/yaya/config.toml``
→ built-in defaults. Secrets are redacted by name regex before they
reach stdout so dumping the config in a bug report is safe.

There is deliberately no ``yaya config set`` / ``edit`` — yaya treats
the TOML file as user-owned. Edit it with ``$EDITOR``; rerun ``yaya
config show`` to verify.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from yaya.cli import CLIState
from yaya.cli.output import emit_ok
from yaya.kernel import load_config

EXAMPLES = """
Examples:
  yaya config show
  yaya --json config show
"""

# Anything matching this regex (case-insensitive) is rendered as ``***``.
# Conservative: better to over-redact in a public log than leak a key.
_SECRET_KEY_RE = re.compile(r".*(token|key|secret|password|passphrase).*", re.IGNORECASE)
_REDACTED = "***"

_stdout = Console()


def _is_secret_key(key: str) -> bool:
    """Return True when ``key`` looks like it names a secret value."""
    return bool(_SECRET_KEY_RE.match(key))


def redact(value: Any) -> Any:
    """Recursively redact secret-looking keys in mappings.

    Mappings are walked; a key matching :data:`_SECRET_KEY_RE` has its
    value replaced with ``"***"`` regardless of type. Lists are walked
    as well so nested structures (``[{"api_key": "sk-..."}]``) get the
    same treatment. Scalars pass through unchanged.

    Returns a freshly-built structure — the input is never mutated.
    """
    # Tool disagreement: mypy narrows isinstance(Mapping/list) preserving
    # Any, pyright narrows to Mapping[Unknown, Unknown] / list[Unknown].
    # Mirror the suppression pattern used in ``yaya/kernel/payload.py``:
    # pyright-specific noqa, no mypy-side type: ignore needed.
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():  # pyright: ignore[reportUnknownVariableType]
            key_str = str(k)  # pyright: ignore[reportUnknownArgumentType]
            out[key_str] = _REDACTED if _is_secret_key(key_str) else redact(v)
        return out
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            result.append(redact(item))
        return result
    return value


def _render_table(config: Mapping[str, Any]) -> Table:
    """Render the resolved config as a two-column rich table."""
    table = Table(title="yaya config (resolved)", show_lines=False)
    table.add_column("key", style="bold cyan")
    table.add_column("value")

    def _walk(prefix: str, node: Any) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():  # pyright: ignore[reportUnknownVariableType]
                child = f"{prefix}.{k}" if prefix else str(k)  # pyright: ignore[reportUnknownArgumentType]
                _walk(child, v)
        else:
            table.add_row(prefix, repr(node))

    _walk("", config)
    return table


def register(app: typer.Typer) -> None:
    """Register the ``config`` subcommand group onto ``app``."""
    config_app = typer.Typer(
        name="config",
        help="Inspect resolved yaya configuration.",
        no_args_is_help=True,
    )

    @config_app.command(name="show", epilog=EXAMPLES)
    def show(ctx: typer.Context) -> None:
        """Print the resolved config (env → toml → defaults), with secrets redacted."""
        state: CLIState = ctx.obj
        kernel_config = load_config()
        # Include both declared kernel fields AND extras (plugin
        # namespaces) so `config show` matches what plugins actually
        # observe via ctx.config.
        raw: dict[str, Any] = kernel_config.model_dump()
        extras = kernel_config.model_extra or {}
        for k, v in extras.items():
            raw.setdefault(k, v)

        redacted = redact(raw)

        if state.json_output:
            emit_ok(state, text="", action="config.show", config=redacted)
            return

        _stdout.print(_render_table(redacted))

    app.add_typer(config_app)
