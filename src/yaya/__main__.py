from __future__ import annotations

import typer
from rich.console import Console

from yaya import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="yaya — a Python agent.")
console = Console()


@app.command()
def version() -> None:
    """Print the current version."""
    console.print(f"yaya [bold cyan]{__version__}[/]")


@app.command()
def hello(name: str = "world") -> None:
    """Say hello."""
    console.print(f"Hello, [bold green]{name}[/]!")


if __name__ == "__main__":
    app()
