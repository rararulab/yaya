"""SQLite-backed memory plugin (stdlib only).

Stores conversation entries under ``ctx.state_dir / "memory.db"`` and
answers ``memory.query`` with the top-``k`` entries whose ``text`` matches
a ``LIKE`` pattern, ordered by recency. Uses :mod:`sqlite3` via
:func:`asyncio.to_thread` to keep the event loop non-blocking; no
third-party drivers.
"""

from yaya.plugins.memory_sqlite.plugin import SqliteMemory

plugin: SqliteMemory = SqliteMemory()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["SqliteMemory", "plugin"]
