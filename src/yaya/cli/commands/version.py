from __future__ import annotations

import typer

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit


def register(app: typer.Typer) -> None:
    @app.command()
    def version(ctx: typer.Context) -> None:
        """Print the current version."""
        state: CLIState = ctx.obj
        emit(
            state,
            text=f"yaya [bold cyan]{__version__}[/]",
            data={"version": __version__},
        )
