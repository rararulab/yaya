from __future__ import annotations

import typer

from yaya.cli import CLIState
from yaya.cli.output import emit


def register(app: typer.Typer) -> None:
    @app.command()
    def hello(
        ctx: typer.Context,
        name: str = typer.Option("world", "--name", "-n", help="Who to greet."),
    ) -> None:
        """Say hello."""
        state: CLIState = ctx.obj
        emit(
            state,
            text=f"Hello, [bold green]{name}[/]!",
            data={"greeting": f"Hello, {name}!", "name": name},
        )
