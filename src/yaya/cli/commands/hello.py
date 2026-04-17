from __future__ import annotations

import typer

from yaya.cli import CLIState
from yaya.cli.output import emit_ok

EXAMPLES = """
Examples:
  yaya hello
  yaya hello --name yaya
  yaya --json hello -n yaya
"""


def register(app: typer.Typer) -> None:
    @app.command(epilog=EXAMPLES)
    def hello(
        ctx: typer.Context,
        name: str = typer.Option("world", "--name", "-n", help="Who to greet."),
    ) -> None:
        """Say hello."""
        state: CLIState = ctx.obj
        emit_ok(
            state,
            text=f"Hello, [bold green]{name}[/]!",
            action="hello",
            name=name,
            greeting=f"Hello, {name}!",
        )
