"""CLI app factory and root callback.

Command modules register themselves onto the app. Shared options
(`--json`, `--verbose`, `--quiet`) are parsed here and placed on
``ctx.obj`` as a :class:`CLIState`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import typer
from loguru import logger

from yaya import __version__

app = typer.Typer(
    name="yaya",
    help="yaya — a Python agent.",
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@dataclass
class CLIState:
    json_output: bool = False
    verbose: int = 0
    quiet: bool = False


def _configure_logging(state: CLIState) -> None:
    logger.remove()
    if state.quiet:
        return
    level = "WARNING"
    if state.verbose == 1:
        level = "INFO"
    elif state.verbose >= 2:
        level = "DEBUG"
    logger.add(sys.stderr, level=level, format="{level: <8} {message}")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout instead of rich text.",
    ),
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Increase log verbosity (-v info, -vv debug).",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Suppress all log output.",
    ),
    version_flag: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
        is_eager=True,
    ),
) -> None:
    if version_flag:
        typer.echo(__version__)
        raise typer.Exit
    state = CLIState(json_output=json_output, verbose=verbose, quiet=quiet)
    _configure_logging(state)
    ctx.obj = state

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit

    if ctx.invoked_subcommand not in {"update", "version"}:
        from yaya.cli.commands.update import maybe_show_update_toast

        maybe_show_update_toast(state)


# Register subcommands. Imported here (not at top) so the app is fully
# constructed before modules touch it.
from yaya.cli.commands import hello, update, version  # noqa: E402

hello.register(app)
update.register(app)
version.register(app)
