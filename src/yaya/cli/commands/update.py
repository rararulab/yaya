from __future__ import annotations

import os
import sys
import time

import typer

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit, warn
from yaya.core import updater
from yaya.core.updater import UpdateResult


def maybe_show_update_toast(state: CLIState) -> None:
    """Non-blocking one-line notice if the cached check shows a newer version.

    Silent when stdout is not a tty, --json is in effect, or the
    YAYA_NO_AUTO_UPDATE env var is set. Refreshes the cache in a
    background thread when stale.
    """
    if state.json_output:
        return
    if os.environ.get("YAYA_NO_AUTO_UPDATE"):
        return
    if not sys.stdout.isatty():
        return

    cached = updater.read_cached_latest()
    stale = not cached or (time.time() - cached[1]) > updater.CACHE_TTL_SECONDS
    if stale:
        updater.spawn_background_refresh()
    if not cached:
        return

    latest, _ = cached
    if updater.semver_tuple(latest) <= updater.semver_tuple(__version__):
        return
    if updater.is_skipped(latest):
        return

    warn(
        f"[yellow]yaya {latest} is available[/] (current: {__version__}). "
        f"Run [bold]{updater.upgrade_hint()}[/] to update."
    )


def register(app: typer.Typer) -> None:
    @app.command()
    def update(
        ctx: typer.Context,
        check: bool = typer.Option(False, "--check", help="Only check, do not install."),
        skip: bool = typer.Option(False, "--skip", help="Skip the current latest version."),
    ) -> None:
        """Check for and apply updates from GitHub releases."""
        state: CLIState = ctx.obj

        with updater.new_http_client() as client:
            if skip:
                latest = updater.fetch_latest_version(client)
                if not latest:
                    emit(
                        state,
                        text="[red]Failed to fetch latest version.[/]",
                        data={"result": "FAILED", "message": "fetch failed"},
                    )
                    raise typer.Exit(1)
                updater.skip_version(latest)
                emit(
                    state,
                    text=f"Skipped version [bold]{latest}[/].",
                    data={"result": "SKIPPED", "version": latest},
                )
                return

            status = updater.check_for_updates(client)
            target = updater.detect_target()

            if status.result == UpdateResult.FAILED:
                emit(state, text=f"[red]{status.message}[/]", data=status.to_dict())
                raise typer.Exit(1)

            if status.result == UpdateResult.UP_TO_DATE:
                emit(state, text=f"[green]{status.message}[/]", data=status.to_dict())
                return

            # UPDATE_AVAILABLE from here on
            if check:
                emit(
                    state,
                    text=f"[yellow]{status.message}[/] (current: {status.current_version})",
                    data=status.to_dict(),
                )
                return

            if not updater.is_frozen():
                text = (
                    f"[yellow]Update available: {status.latest_version}[/] "
                    f"(current: {status.current_version})\n"
                    f"Installed via pip/uv; run: [bold]{updater.upgrade_hint()}[/]"
                )
                emit(state, text=text, data=status.to_dict())
                return

            if not target or "windows" in target:
                msg = (
                    "Auto-update not supported on this platform. "
                    f"Download {status.latest_version} manually from "
                    f"https://github.com/{updater.REPO}/releases/latest"
                )
                emit(state, text=f"[red]{msg}[/]", data=status.to_dict())
                raise typer.Exit(1)

            applied = updater.apply_update(client, target, status.latest_version or "")
            if applied.result == UpdateResult.UPDATED:
                emit(state, text=f"[green]{applied.message}[/]", data=applied.to_dict())
            else:
                emit(state, text=f"[red]{applied.message}[/]", data=applied.to_dict())
                raise typer.Exit(1)
