"""SQLite memory plugin implementation.

Schema::

    CREATE TABLE memory (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        meta JSON,
        ts REAL
    )

Handlers dispatch synchronous :mod:`sqlite3` calls through
:func:`asyncio.to_thread` so a slow disk does not stall the event bus.
Connection is per-plugin-instance and therefore NOT shared across
sessions — ``sqlite3.Connection`` is not safe to use from multiple
threads simultaneously without ``check_same_thread=False`` plus a
serialization discipline we do not want here.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

_NAME = "memory-sqlite"
_VERSION = "0.1.0"
_DB_FILENAME = "memory.db"
_DEFAULT_K = 5
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    meta TEXT,
    ts REAL
)
"""


class SqliteMemory:
    """Bundled stdlib-only memory plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.MEMORY`.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.MEMORY
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._db_path: Path | None = None

    def subscriptions(self) -> list[str]:
        """Memory category handles both query and write requests."""
        return ["memory.query", "memory.write"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Open the per-plugin sqlite db and ensure the schema exists."""
        db_path = ctx.state_dir / _DB_FILENAME
        self._db_path = db_path
        self._conn = await asyncio.to_thread(_open_db, db_path)
        ctx.logger.debug("memory-sqlite loaded at %s", db_path)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Dispatch to the right handler by event kind."""
        if self._conn is None:
            raise RuntimeError("memory-sqlite received event before on_load")
        if ev.kind == "memory.query":
            await self._handle_query(ev, ctx)
        elif ev.kind == "memory.write":
            await self._handle_write(ev, ctx)

    async def on_unload(self, ctx: KernelContext) -> None:
        """Close the sqlite connection. Idempotent."""
        conn, self._conn = self._conn, None
        if conn is not None:
            await asyncio.to_thread(conn.close)

    # -- handlers -------------------------------------------------------------

    async def _handle_query(self, ev: Event, ctx: KernelContext) -> None:
        """Run a ``LIKE`` query, return top-k by recency."""
        assert self._conn is not None  # noqa: S101 - module-level invariant, not user input.
        query = str(ev.payload.get("query", ""))
        k_raw = ev.payload.get("k", _DEFAULT_K)
        try:
            k = int(k_raw) if k_raw is not None else _DEFAULT_K
        except TypeError, ValueError:
            k = _DEFAULT_K
        if k <= 0:
            k = _DEFAULT_K
        rows = await asyncio.to_thread(_query_like, self._conn, query, k)
        hits = [_row_to_entry(row) for row in rows]
        await ctx.emit(
            "memory.result",
            {"hits": hits, "request_id": ev.id},
            session_id=ev.session_id,
        )

    async def _handle_write(self, ev: Event, ctx: KernelContext) -> None:
        """Insert one entry; log WARNING on duplicate id, DEBUG on success."""
        assert self._conn is not None  # noqa: S101
        raw_entry = ev.payload.get("entry")
        if not isinstance(raw_entry, dict):
            # Protocol violation: entry is required per plugin-protocol.md.
            raise ValueError("memory.write missing 'entry' dict")  # noqa: TRY004
        entry: dict[str, Any] = cast("dict[str, Any]", raw_entry)
        entry_id = str(entry.get("id") or uuid.uuid4().hex)
        text = str(entry.get("text", ""))
        meta = entry.get("meta")
        meta_json = json.dumps(meta) if meta is not None else None
        ts = float(entry.get("ts") or time.time())

        try:
            await asyncio.to_thread(_insert_entry, self._conn, entry_id, text, meta_json, ts)
        except sqlite3.IntegrityError as exc:
            # Duplicate id — surface a WARNING so the author sees the collision.
            ctx.logger.warning("memory.write duplicate id %r: %s", entry_id, exc)
            return
        ctx.logger.debug("memory.write persisted id=%s (len=%d)", entry_id, len(text))


# ---------------------------------------------------------------------------
# Synchronous sqlite helpers. Kept module-level so ``asyncio.to_thread`` has a
# picklable callable and tests can exercise them directly.
# ---------------------------------------------------------------------------


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open ``db_path`` and ensure the schema exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _insert_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    text: str,
    meta_json: str | None,
    ts: float,
) -> None:
    """Insert one row; raise :class:`sqlite3.IntegrityError` on duplicate id."""
    conn.execute(
        "INSERT INTO memory(id, text, meta, ts) VALUES (?, ?, ?, ?)",
        (entry_id, text, meta_json, ts),
    )
    conn.commit()


def _query_like(conn: sqlite3.Connection, query: str, k: int) -> list[tuple[str, str, str | None, float]]:
    """Return the top-``k`` rows matching ``text LIKE %query%`` by recency.

    An empty query matches everything (useful for "tail the most recent
    N entries" semantics).
    """
    pattern = f"%{query}%" if query else "%"
    cur = conn.execute(
        "SELECT id, text, meta, ts FROM memory WHERE text LIKE ? ORDER BY ts DESC LIMIT ?",
        (pattern, k),
    )
    rows: list[tuple[str, str, str | None, float]] = []
    for row in cur.fetchall():
        rows.append((str(row[0]), str(row[1]), row[2], float(row[3])))
    return rows


def _row_to_entry(
    row: tuple[str, str, str | None, float],
) -> dict[str, Any]:
    """Convert a ``(id, text, meta_json, ts)`` row to a ``MemoryEntry`` dict."""
    entry_id, text, meta_json, _ts = row
    entry: dict[str, Any] = {"id": entry_id, "text": text}
    if meta_json is not None:
        try:
            entry["meta"] = json.loads(meta_json)
        except json.JSONDecodeError:
            # Corrupt row — return the raw string so the caller can see it
            # without masking the issue.
            entry["meta"] = {"_raw": meta_json}
    return entry


__all__ = ["SqliteMemory"]
