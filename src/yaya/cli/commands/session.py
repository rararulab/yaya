"""``yaya session list|show|resume|archive`` — tape-backed session CLI (#32).

Every subcommand boots a **transient** :class:`~yaya.kernel.SessionStore`,
performs its work, and tears down before returning. No long-lived
singletons live in the CLI layer; the kernel boot path inside
``yaya serve`` owns the runtime store.

``yaya session resume <id>`` prints resume metadata and exits —
actual resume happens inside ``yaya serve --resume <id>`` which wires
the same id into the kernel boot path.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console
from rich.table import Table

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok
from yaya.kernel import SessionInfo, SessionStore, load_config, tape_name_for

if TYPE_CHECKING:  # pragma: no cover - type-only import
    pass

EXAMPLES_LIST = """
Examples:
  yaya session list
  yaya --json session list
"""

EXAMPLES_SHOW = """
Examples:
  yaya session show default
  yaya session show default --tail 20
  yaya --json session show default
"""

EXAMPLES_RESUME = """
Examples:
  yaya session resume default
  yaya --json session resume default
"""

EXAMPLES_ARCHIVE = """
Examples:
  yaya session archive default
  yaya --json session archive default --yes
"""

_stdout = Console()


def register(app: typer.Typer) -> None:  # noqa: C901 — four sibling Typer commands
    """Register the ``session`` subcommand group onto ``app``."""
    session_app = typer.Typer(
        name="session",
        help="Manage session tapes (persisted append-only event log).",
        no_args_is_help=True,
    )

    @session_app.command("list", epilog=EXAMPLES_LIST)
    def session_list(ctx: typer.Context) -> None:
        """List sessions with a tape in the current workspace."""
        state: CLIState = ctx.obj
        rows = asyncio.run(_list_sessions())
        if state.json_output:
            emit_ok(state, action="session.list", sessions=rows)
            return
        _stdout.print(_render_session_table(rows))

    @session_app.command("show", epilog=EXAMPLES_SHOW)
    def session_show(
        ctx: typer.Context,
        session_id: str = typer.Argument(..., help="Session id to show."),
        tail: int = typer.Option(
            50,
            "--tail",
            "-n",
            min=1,
            max=10_000,
            help="Show the last N tape entries.",
        ),
    ) -> None:
        """Tail the tape for ``session_id`` in the current workspace."""
        state: CLIState = ctx.obj
        entries = asyncio.run(_show_session(session_id, tail=tail))
        if state.json_output:
            emit_ok(
                state,
                action="session.show",
                session_id=session_id,
                entries=entries,
            )
            return
        for entry in entries:
            _stdout.print_json(json.dumps(entry))

    @session_app.command("resume", epilog=EXAMPLES_RESUME)
    def session_resume(
        ctx: typer.Context,
        session_id: str = typer.Argument(..., help="Session id to resume."),
    ) -> None:
        """Mark ``session_id`` as the resume target for the next ``yaya serve``."""
        state: CLIState = ctx.obj
        info = asyncio.run(_resume_session(session_id))
        if info is None:
            emit_error(
                state,
                error=f"session {session_id!r} not found in this workspace",
                suggestion="run `yaya session list` to see available ids",
            )
            raise typer.Exit(1)
        if state.json_output:
            emit_ok(
                state,
                action="session.resume",
                session_id=session_id,
                tape_name=info.tape_name,
                entry_count=info.entry_count,
            )
            return
        _stdout.print(
            f"[green]resume target set[/] for session [bold]{session_id}[/] "
            f"(tape {info.tape_name}, {info.entry_count} entries). "
            f"Run `yaya serve --resume {session_id}` to continue the conversation."
        )

    @session_app.command("archive", epilog=EXAMPLES_ARCHIVE)
    def session_archive(
        ctx: typer.Context,
        session_id: str = typer.Argument(..., help="Session id to archive."),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help="Skip the interactive confirmation.",
        ),
    ) -> None:
        """Archive + reset the tape for ``session_id``."""
        state: CLIState = ctx.obj
        if state.json_output and not yes:
            emit_error(
                state,
                error="confirmation_required",
                suggestion="pass --yes for unattended archive",
            )
            raise typer.Exit(1)
        if not yes and not typer.confirm(
            f"archive session {session_id!r}?",
            default=False,
        ):
            emit_ok(state, text="[dim]aborted.[/]", action="session.aborted")
            return
        archive_path = asyncio.run(_archive_session(session_id))
        emit_ok(
            state,
            text=f"[green]archived[/] session {session_id} → {archive_path}",
            action="session.archive",
            session_id=session_id,
            archive_path=str(archive_path),
        )

    app.add_typer(session_app, name="session")


# ---------------------------------------------------------------------------
# Async helpers.
# ---------------------------------------------------------------------------


def _workspace() -> Path:
    return Path.cwd()


def _make_store() -> SessionStore:
    cfg = load_config()
    if cfg.session.store == "memory":
        from yaya.kernel import MemoryTapeStore

        return SessionStore(store=MemoryTapeStore())
    # File-backed store. Honour session.dir override if set; fall back to
    # the XDG-derived default baked into SessionStore.__init__.
    if cfg.session.dir is not None:
        return SessionStore(tapes_dir=cfg.session.dir)
    return SessionStore()


async def _list_sessions() -> list[dict[str, Any]]:
    store = _make_store()
    try:
        infos = await store.list_sessions(_workspace())
    finally:
        await store.close()
    return [_info_dict(info) for info in infos]


async def _show_session(session_id: str, *, tail: int) -> list[dict[str, Any]]:
    store = _make_store()
    try:
        session = await store.open(_workspace(), session_id)
        # ``Session.tail`` streams the jsonl file through a bounded deque
        # on the file store, so ``--tail 5`` on a 10 MB tape is O(n)-tail-memory
        # instead of buffering every entry via ``entries()``.
        sliced = await session.tail(tail) if tail > 0 else await session.entries()
    finally:
        await store.close()
    return [
        {
            "id": e.id,
            "kind": e.kind,
            "payload": e.payload,
            "meta": e.meta,
            "date": e.date,
        }
        for e in sliced
    ]


async def _resume_session(session_id: str) -> SessionInfo | None:
    store = _make_store()
    try:
        infos = await store.list_sessions(_workspace())
        target = tape_name_for(_workspace(), session_id)
        for info in infos:
            if info.tape_name == target:
                # Persist the resume choice via an environment-style
                # hand-off so ``yaya serve --resume <id>`` on a fresh
                # invocation can pick it up.
                _write_resume_marker(session_id)
                return info
        return None
    finally:
        await store.close()


async def _archive_session(session_id: str) -> Path:
    store = _make_store()
    try:
        session = await store.open(_workspace(), session_id)
        archive = await session.reset(archive=True)
    finally:
        await store.close()
    assert archive is not None  # noqa: S101 — reset(archive=True) always returns a path.
    return archive


def _write_resume_marker(session_id: str) -> None:
    """Drop a small marker file so ``serve --resume`` can auto-pick it.

    The marker is plain-text so humans can inspect / delete it. Uses
    ``YAYA_STATE_DIR``/``XDG_STATE_HOME`` per the session directory
    rule for consistency.
    """
    from yaya.kernel import default_session_dir

    path = default_session_dir().parent / "resume.target"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id, encoding="utf-8")
    if os.name == "posix":
        # POSIX only; Windows treats chmod as a no-op beyond the read-only bit.
        os.chmod(path, 0o600)


def _info_dict(info: SessionInfo) -> dict[str, Any]:
    return {
        "session_id": info.session_id,
        "tape_name": info.tape_name,
        "created_at": info.created_at,
        "entry_count": info.entry_count,
        "last_anchor": info.last_anchor,
    }


def _render_session_table(rows: list[dict[str, Any]]) -> Table:
    table = Table(title="yaya sessions")
    table.add_column("session", style="bold")
    table.add_column("tape")
    table.add_column("entries", justify="right")
    table.add_column("last anchor")
    table.add_column("created")
    for row in rows:
        table.add_row(
            str(row.get("session_id", "")),
            str(row.get("tape_name", "")),
            str(row.get("entry_count", "")),
            str(row.get("last_anchor") or "-"),
            str(row.get("created_at", "")),
        )
    return table
