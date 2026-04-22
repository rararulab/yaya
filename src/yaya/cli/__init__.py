"""CLI app factory and root callback.

Command modules register themselves onto the app. Shared options
(`--json`, `--verbose`, `--quiet`) are parsed here and placed on
``ctx.obj`` as a :class:`CLIState`.
"""

from __future__ import annotations

from dataclasses import dataclass

import typer
from loguru import logger

from yaya import __version__
from yaya.kernel import KernelConfig, configure_logging, load_config

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
    config: KernelConfig | None = None


def _resolve_log_level(
    *,
    base: str,
    log_level_flag: str | None,
    verbose: int,
    quiet: bool,
) -> str:
    """Reconcile the explicit ``--log-level`` flag with ``-v`` / ``-q``.

    Precedence (highest first): ``--quiet`` short-circuits to CRITICAL,
    then ``--log-level`` if provided, then ``-v`` count, then the
    config-resolved default. Keeping the legacy verbosity flags makes
    the agent-friendly CLI table unchanged for downstream scripts.
    """
    if quiet:
        return "CRITICAL"
    if log_level_flag is not None:
        return log_level_flag.upper()
    if verbose >= 2:
        return "DEBUG"
    if verbose == 1:
        return "INFO"
    return base.upper()


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
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Override log level (DEBUG, INFO, WARNING, ERROR). Defaults to config.",
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
    config = load_config()
    config.log_level = _resolve_log_level(
        base=config.log_level,
        log_level_flag=log_level,
        verbose=verbose,
        quiet=quiet,
    )
    configure_logging(config)
    state = CLIState(json_output=json_output, verbose=verbose, quiet=quiet, config=config)
    # Stash on ctx.obj so subcommands reuse the same resolved config
    # without re-running the loader (deterministic but env-touching).
    ctx.obj = state
    _ = logger  # keep import alive: re-exported for downstream `from yaya.cli import logger`

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit

    if ctx.invoked_subcommand not in {"update", "version", "doctor", "serve", "plugin", "config", "session"}:
        from yaya.cli.commands.update import maybe_show_update_toast

        maybe_show_update_toast(state)


# Register subcommands. Imported here (not at top) so the app is fully
# constructed before modules touch it.
from yaya.cli.commands import config, doctor, plugin, serve, session, update, version  # noqa: E402

config.register(app)
doctor.register(app)
plugin.register(app)
serve.register(app)
session.register(app)
update.register(app)
version.register(app)
