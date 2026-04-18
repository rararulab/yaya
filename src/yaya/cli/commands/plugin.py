"""``yaya plugin list|install|remove`` — thin wrappers over the registry.

Every subcommand boots a **transient** kernel (bus + registry),
performs its work, and tears down before returning. There is no shared
registry singleton; the CLI owns its own short-lived stack. See
``docs/dev/cli.md`` for the convention.

Destructive subcommands (``install`` / ``remove``) honour the
``--dry-run`` and ``--yes`` flags from the org CLI spec. Under
``--json`` we refuse to prompt — unattended operation requires
``--yes``, otherwise the command emits a machine-readable
``confirmation_required`` error.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok
from yaya.kernel import EventBus, PluginRegistry, validate_install_source

EXAMPLES_LIST = """
Examples:
  yaya plugin list
  yaya --json plugin list
"""

EXAMPLES_INSTALL = """
Examples:
  yaya plugin install yaya-tool-bash
  yaya plugin install ./my-plugin --editable
  yaya --json plugin install yaya-tool-bash --yes
"""

EXAMPLES_REMOVE = """
Examples:
  yaya plugin remove yaya-tool-bash
  yaya --json plugin remove yaya-tool-bash --yes
"""

_stdout = Console()


def _render_plugin_table(rows: list[dict[str, str]]) -> Table:
    """Return a rich table rendering of ``registry.snapshot()`` output."""
    table = Table(title="yaya plugins")
    table.add_column("name", style="bold")
    table.add_column("version")
    table.add_column("category")
    table.add_column("status")
    for row in rows:
        table.add_row(
            row.get("name", ""),
            row.get("version", ""),
            row.get("category", ""),
            row.get("status", ""),
        )
    return table


def _require_yes_under_json(state: CLIState, yes: bool) -> bool:
    """Return True if unattended mode should abort for lack of ``--yes``.

    Under ``--json`` we never prompt. Emits the error; caller exits.
    """
    if state.json_output and not yes:
        emit_error(
            state,
            error="confirmation_required",
            suggestion="pass --yes for unattended install/remove",
        )
        return True
    return False


def _confirm_or_abort(state: CLIState, *, prompt: str, yes: bool) -> bool:
    """Prompt interactively unless ``yes`` is set.

    Returns:
        True when the caller should proceed; False when the user
        declined (caller exits 0 with an ``aborted`` result).
    """
    if yes:
        return True
    if not typer.confirm(prompt, default=False):
        emit_ok(state, text="[dim]aborted.[/]", action="plugin.aborted")
        return False
    return True


def register(app: typer.Typer) -> None:  # noqa: C901 — three sibling Typer commands
    """Register the ``plugin`` subcommand group onto ``app``."""
    plugin_app = typer.Typer(
        name="plugin",
        help="Manage installed plugins.",
        no_args_is_help=True,
    )

    @plugin_app.command("list", epilog=EXAMPLES_LIST)
    def plugin_list(ctx: typer.Context) -> None:
        """List every discovered plugin with its category and status."""
        state: CLIState = ctx.obj
        rows = asyncio.run(_list_plugins())
        if state.json_output:
            emit_ok(state, action="plugin.list", plugins=rows)
            return
        _stdout.print(_render_plugin_table(rows))

    @plugin_app.command("install", epilog=EXAMPLES_INSTALL)
    def plugin_install(
        ctx: typer.Context,
        source: str = typer.Argument(..., help="PyPI name, absolute path, file:// or https:// URL."),
        editable: bool = typer.Option(
            False,
            "--editable",
            "-e",
            help="Pass `pip install -e` for path / file:// sources.",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help="Skip the interactive confirmation.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Validate and confirm, but do not run pip install.",
        ),
    ) -> None:
        """Install a plugin distribution and refresh the registry."""
        state: CLIState = ctx.obj

        try:
            validate_install_source(source)
        except ValueError as exc:
            emit_error(
                state,
                error=str(exc),
                suggestion="pass a PyPI name, absolute path, file:// URL, or https:// URL",
            )
            raise typer.Exit(1) from exc

        if _require_yes_under_json(state, yes):
            raise typer.Exit(1)

        if not _confirm_or_abort(
            state,
            prompt=f"install {source!r} (editable={editable})?",
            yes=yes,
        ):
            return

        if dry_run:
            emit_ok(
                state,
                text=f"[dim]dry-run:[/] would install {source!r} (editable={editable})",
                action="plugin.install",
                dry_run=True,
                source=source,
                editable=editable,
            )
            return

        try:
            asyncio.run(_install_plugin(source, editable=editable))
        except ValueError as exc:
            emit_error(
                state,
                error=str(exc),
                suggestion="fix the source and retry",
            )
            raise typer.Exit(1) from exc
        except RuntimeError as exc:
            emit_error(
                state,
                error=str(exc),
                suggestion="inspect the pip stderr above and retry",
            )
            raise typer.Exit(1) from exc

        emit_ok(
            state,
            text=f"[green]installed[/] {source}",
            action="plugin.install",
            source=source,
            editable=editable,
        )

    @plugin_app.command("remove", epilog=EXAMPLES_REMOVE)
    def plugin_remove(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Plugin name to uninstall."),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help="Skip the interactive confirmation.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Validate and confirm, but do not run pip uninstall.",
        ),
    ) -> None:
        """Uninstall a plugin distribution and refresh the registry."""
        state: CLIState = ctx.obj

        if _require_yes_under_json(state, yes):
            raise typer.Exit(1)

        if not _confirm_or_abort(
            state,
            prompt=f"remove plugin {name!r}?",
            yes=yes,
        ):
            return

        if dry_run:
            emit_ok(
                state,
                text=f"[dim]dry-run:[/] would remove {name!r}",
                action="plugin.remove",
                dry_run=True,
                name=name,
            )
            return

        try:
            asyncio.run(_remove_plugin(name))
        except ValueError as exc:
            emit_error(
                state,
                error=str(exc),
                suggestion=("bundled plugins ship with yaya; use `yaya update` to move to a different release"),
            )
            raise typer.Exit(1) from exc
        except RuntimeError as exc:
            emit_error(
                state,
                error=str(exc),
                suggestion="inspect the pip stderr above and retry",
            )
            raise typer.Exit(1) from exc

        emit_ok(
            state,
            text=f"[green]removed[/] {name}",
            action="plugin.remove",
            name=name,
        )

    app.add_typer(plugin_app, name="plugin")


# ---------------------------------------------------------------------------
# Async helpers. Split out so tests can target them without going through
# the Typer command surface.
# ---------------------------------------------------------------------------


async def _list_plugins() -> list[dict[str, str]]:
    """Boot a transient registry and return its snapshot.

    Constructions live inside the ``try`` so a failure from
    ``registry.start()`` still runs ``bus.close()`` — otherwise the
    started bus (and any spawned adapter uvicorn tasks) would leak.
    """
    bus = EventBus()
    registry = PluginRegistry(bus)
    registry_started = False
    try:
        await registry.start()
        registry_started = True
        return registry.snapshot()
    finally:
        if registry_started:
            await registry.stop()
        await bus.close()


async def _install_plugin(source: str, *, editable: bool) -> None:
    """Boot a transient registry and run ``registry.install``."""
    bus = EventBus()
    registry = PluginRegistry(bus)
    registry_started = False
    try:
        await registry.start()
        registry_started = True
        await registry.install(source, editable=editable)
    finally:
        if registry_started:
            await registry.stop()
        await bus.close()


async def _remove_plugin(name: str) -> None:
    """Boot a transient registry and run ``registry.remove``."""
    bus = EventBus()
    registry = PluginRegistry(bus)
    registry_started = False
    try:
        await registry.start()
        registry_started = True
        await registry.remove(name)
    finally:
        if registry_started:
            await registry.stop()
        await bus.close()
