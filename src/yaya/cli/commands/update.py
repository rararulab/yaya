from __future__ import annotations

import os
import sys
import time

import typer

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok, warn
from yaya.core import updater
from yaya.core.updater import UpdateResult

EXAMPLES = """
Examples:
  yaya update --check
  yaya update --check --json
  yaya update --skip
  yaya update
"""


def maybe_show_update_toast(state: CLIState) -> None:
    """Non-blocking stderr notice when a cached check shows a newer version.

    Suppressed under ``--json``, when stdout is not a tty, or when
    ``YAYA_NO_AUTO_UPDATE`` is set. Refreshes the cache in a background
    thread when stale.
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


def _suggestion_for_not_frozen() -> str:
    return f"run `{updater.upgrade_hint()}`"


def register(app: typer.Typer) -> None:
    @app.command(epilog=EXAMPLES)
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
                    emit_error(
                        state,
                        error="failed to fetch latest release from github",
                        suggestion="check network connectivity and retry",
                    )
                    raise typer.Exit(1)
                updater.skip_version(latest)
                emit_ok(
                    state,
                    text=f"Skipped version [bold]{latest}[/].",
                    action="update.skip",
                    skipped_version=latest,
                )
                return

            status = updater.check_for_updates(client)
            target = updater.detect_target()

            if status.result == UpdateResult.FAILED:
                emit_error(
                    state,
                    error=status.message,
                    suggestion="check network connectivity and retry",
                    current_version=status.current_version,
                )
                raise typer.Exit(1)

            if status.result == UpdateResult.UP_TO_DATE:
                emit_ok(
                    state,
                    text=f"[green]{status.message}[/]",
                    action="update.check",
                    up_to_date=True,
                    current_version=status.current_version,
                    latest_version=status.latest_version,
                )
                return

            # UPDATE_AVAILABLE from here on.
            if check:
                emit_ok(
                    state,
                    text=(f"[yellow]Update available: {status.latest_version}[/] (current: {status.current_version})"),
                    action="update.check",
                    up_to_date=False,
                    current_version=status.current_version,
                    latest_version=status.latest_version,
                    suggestion=f"run `yaya update` to install {status.latest_version}",
                )
                return

            if not updater.is_frozen():
                emit_ok(
                    state,
                    text=(
                        f"[yellow]Update available: {status.latest_version}[/] "
                        f"(current: {status.current_version})\n"
                        f"Installed via pip/uv; run: [bold]{updater.upgrade_hint()}[/]"
                    ),
                    action="update.check",
                    up_to_date=False,
                    current_version=status.current_version,
                    latest_version=status.latest_version,
                    suggestion=_suggestion_for_not_frozen(),
                    install_method="pip-or-uv",
                )
                return

            if not target or "windows" in target:
                emit_error(
                    state,
                    error="auto-update is not supported on this platform",
                    suggestion=(
                        f"download {status.latest_version} from https://github.com/{updater.REPO}/releases/latest"
                    ),
                    latest_version=status.latest_version,
                )
                raise typer.Exit(1)

            applied = updater.apply_update(client, target, status.latest_version or "")
            if applied.result == UpdateResult.UPDATED:
                emit_ok(
                    state,
                    text=f"[green]{applied.message}[/]",
                    action="update.apply",
                    current_version=applied.current_version,
                    latest_version=applied.latest_version,
                )
            else:
                emit_error(
                    state,
                    error=applied.message,
                    suggestion=(
                        f"retry, or download {applied.latest_version} from "
                        f"https://github.com/{updater.REPO}/releases/latest"
                    ),
                    latest_version=applied.latest_version,
                )
                raise typer.Exit(1)
