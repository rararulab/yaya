from __future__ import annotations

import typer

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit_ok

EXAMPLES = """
Examples:
  yaya version
  yaya --json version
"""


def register(app: typer.Typer) -> None:
    @app.command(epilog=EXAMPLES)
    def version(ctx: typer.Context) -> None:
        """Print the installed version."""
        state: CLIState = ctx.obj
        emit_ok(
            state,
            text=f"yaya [bold cyan]{__version__}[/]",
            action="version",
            version=__version__,
        )
