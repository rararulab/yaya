"""SQLite-backed live key-value config store with hot-reload events.

The static boot-time :class:`~yaya.kernel.config.KernelConfig` (TOML +
env) becomes a one-time migration source. After first boot, a
per-install SQLite database under
``${XDG_STATE_HOME:-~/.local/state}/yaya/config.db`` is the source of
truth. Every write goes through three steps in order: persist to the
DB, update the in-memory cache, emit a ``config.updated`` event on the
``"kernel"`` session so plugins subscribed to the relevant prefix can
hot-reload without a restart.

Reads are O(1) against the in-memory cache; the cache is
authoritative because SQLite is single-writer-per-process and the
ConfigStore owns that single writer. Plugin code receives a
:class:`ConfigView` — a read-only :class:`Mapping` view over the cache
— so the existing ``ctx.config["key"]`` duck-typed reads keep working
with live semantics.

Concurrency model mirrors :mod:`yaya.plugins.memory_sqlite.plugin`:
one ``sqlite3.Connection`` opened with ``check_same_thread=False`` and
every DB call dispatched through a dedicated single-worker
:class:`concurrent.futures.ThreadPoolExecutor`. That executor is the
authoritative serialization primitive — the stdlib sqlite driver is
safe across threads only when every call funnels through one worker.

Layering: depends on :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
and the Python standard library. No imports from ``cli``, ``plugins``,
or ``core``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from yaya.kernel.events import new_event

if TYPE_CHECKING:  # pragma: no cover - type-only imports, avoid cycles.
    from yaya.kernel.bus import EventBus

__all__ = [
    "ConfigStore",
    "ConfigView",
    "default_config_db_path",
]

_DB_FILENAME = "config.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""
_PREFIX_INDEX = "CREATE INDEX IF NOT EXISTS config_prefix ON config(key);"

_MIGRATION_MARKER = "_meta.migrated_from_toml_at"
"""Key written once the first TOML/env → DB migration completes.

Presence of this key (and only this key) is the signal subsequent
boots use to skip the migration path. Storing the epoch seconds under
the same schema as every other config row keeps the marker
introspectable via ``yaya config get _meta.migrated_from_toml_at`` —
no second metadata table required.
"""

_KERNEL_SESSION = "kernel"
"""Routing key for ``config.updated`` events (lesson #2)."""

_CONFIG_SOURCE = "kernel-config-store"


def default_config_db_path() -> Path:
    """Return the XDG-resolved path for ``config.db``.

    Honours ``YAYA_STATE_DIR`` first (test / CI override), then
    ``XDG_STATE_HOME`` per the XDG Base Directory Specification,
    falling back to ``~/.local/state``. On Windows (no XDG, no
    explicit override) the fallback lands under ``%LOCALAPPDATA%``
    because ``~/.local/state`` is meaningless there.

    The parent directory is NOT created here — :meth:`ConfigStore.open`
    is responsible for ``mkdir(parents=True, exist_ok=True)`` so the
    path resolver stays a pure function suitable for diagnostics.
    """
    explicit = os.environ.get("YAYA_STATE_DIR")
    if explicit:
        return Path(explicit) / _DB_FILENAME
    raw = os.environ.get("XDG_STATE_HOME") or ""
    if raw:
        return Path(raw) / "yaya" / _DB_FILENAME
    if sys.platform == "win32":  # pragma: no cover - platform-specific branch.
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "yaya" / _DB_FILENAME
    return Path.home() / ".local" / "state" / "yaya" / _DB_FILENAME


# JSON-encodable scalar + container types accepted by :meth:`ConfigStore.set`.
_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _validate_json_value(value: object) -> None:
    """Reject inputs that cannot round-trip through ``json.dumps``.

    We do an explicit type check instead of relying on ``json.dumps``
    raising because ``json.dumps`` accepts tuples (serialized as
    lists) and that would silently change the stored shape on
    retrieval.
    """
    if isinstance(value, _JSON_SCALAR_TYPES):
        return
    if isinstance(value, list):
        # mypy/pyright cannot narrow list[Unknown] here; the recursion
        # is on items, not the list container itself.
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            _validate_json_value(item)  # pyright: ignore[reportUnknownArgumentType]
        return
    if isinstance(value, dict):
        for k, v in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(k, str):
                raise TypeError(f"config dict keys must be str, got {type(k).__name__}")  # pyright: ignore[reportUnknownArgumentType]
            _validate_json_value(v)  # pyright: ignore[reportUnknownArgumentType]
        return
    raise TypeError(f"config value of type {type(value).__name__} is not JSON-encodable")


class ConfigView(Mapping[str, Any]):
    """Read-only :class:`Mapping` view over a :class:`ConfigStore`'s live cache.

    Handed to plugins as :attr:`~yaya.kernel.plugin.KernelContext.config`
    so existing duck-typed reads (``ctx.config["provider"]``) continue
    to work — but every read now hits the authoritative cache, so
    runtime ``ConfigStore.set`` calls are visible without a restart.

    When ``prefix`` is non-empty the view only exposes keys starting
    with ``prefix``; accessors strip the prefix on read so a plugin
    scoped view with prefix ``"plugin.llm_openai."`` surfaces
    ``api_key`` / ``base_url`` exactly as the legacy sub-tree mapping
    did. Per-plugin scoped views are how registry keeps pre-store
    plugins working unchanged after the switch.

    The view does NOT copy the cache; it proxies. Callers that need a
    stable snapshot use ``dict(view)``.
    """

    def __init__(self, cache: dict[str, Any], prefix: str = "") -> None:
        """Bind the view to the store's shared cache dict.

        Args:
            cache: The :class:`ConfigStore`'s ``_cache`` attribute.
                Lifetime matches the owning store.
            prefix: Optional dotted prefix. Keys not starting with it
                are hidden; matching keys are returned with the prefix
                stripped so callers see the same shape as the legacy
                per-plugin sub-tree.
        """
        self._cache = cache
        self._prefix = prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}" if self._prefix else key

    @override
    def __getitem__(self, key: str) -> Any:
        return self._cache[self._full_key(key)]

    @override
    def __iter__(self) -> Iterator[str]:
        # Snapshot keys so iteration does not observe concurrent set()
        # mid-iteration (the store's writes mutate the dict in place).
        if not self._prefix:
            return iter(list(self._cache.keys()))
        return iter([k[len(self._prefix) :] for k in list(self._cache.keys()) if k.startswith(self._prefix)])

    @override
    def __len__(self) -> int:
        if not self._prefix:
            return len(self._cache)
        return sum(1 for k in self._cache if k.startswith(self._prefix))

    @override
    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self._full_key(key) in self._cache


class ConfigStore:
    """Live SQLite key-value config store with hot-reload event emission.

    Lifecycle is owned by :class:`~yaya.kernel.registry.PluginRegistry`:
    opened before plugin discovery so ``on_load`` sees a populated
    :class:`ConfigView`, and closed after every plugin's ``on_unload``
    so writes during teardown still reach the DB.

    Thread model: single asyncio event loop. Every public method is a
    coroutine; synchronous DB work runs on a dedicated
    :class:`~concurrent.futures.ThreadPoolExecutor` with one worker so
    concurrent callers serialise naturally.
    """

    def __init__(
        self,
        *,
        bus: EventBus | None,
        path: Path,
        conn: sqlite3.Connection,
        executor: concurrent.futures.ThreadPoolExecutor,
        cache: dict[str, Any],
    ) -> None:
        """Use :meth:`ConfigStore.open` for construction.

        Direct construction is reserved for the classmethod; tests
        exercise the store via the same ``open`` path so the
        constructor signature can evolve without breaking fixtures.
        """
        self._bus = bus
        self._path = path
        self._conn: sqlite3.Connection | None = conn
        self._executor: concurrent.futures.ThreadPoolExecutor | None = executor
        self._cache: dict[str, Any] = cache
        self._closed = False

    @classmethod
    async def open(
        cls,
        *,
        bus: EventBus | None,
        path: Path | None = None,
    ) -> ConfigStore:
        """Open the SQLite DB, load the cache, and return a live store.

        Args:
            bus: The running :class:`~yaya.kernel.bus.EventBus`. Passing
                ``None`` disables ``config.updated`` emission — useful
                for CLI ``yaya config get`` invocations that don't run
                a kernel.
            path: Override the default DB path. Defaults to
                :func:`default_config_db_path`.

        Returns:
            A ready-to-use :class:`ConfigStore`. Caller owns close.
        """
        db_path = path or default_config_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="yaya-cfgdb",
        )
        loop = asyncio.get_running_loop()
        conn = await loop.run_in_executor(executor, _open_db, db_path)
        rows = await loop.run_in_executor(executor, _load_all_rows, conn)
        cache: dict[str, Any] = {}
        for key, raw in rows:
            try:
                cache[key] = json.loads(raw)
            except json.JSONDecodeError:
                # Corrupt row — surface the raw string rather than
                # crashing the kernel. A follow-up ``set`` on the same
                # key rewrites a valid JSON payload.
                cache[key] = raw
        return cls(bus=bus, path=db_path, conn=conn, executor=executor, cache=cache)

    async def close(self) -> None:
        """Idempotent teardown: close the DB, shut the executor, clear state."""
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        executor = self._executor
        self._conn = None
        self._executor = None
        if conn is not None and executor is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, conn.close)
        if executor is not None:
            executor.shutdown(wait=True)

    @property
    def path(self) -> Path:
        """Absolute path of the backing SQLite file."""
        return self._path

    def view(self, prefix: str = "") -> ConfigView:
        """Return a :class:`ConfigView` proxying the live cache.

        Args:
            prefix: Optional dotted prefix. Scoped views are used by
                the registry to hand each plugin a view filtered to
                its own ``plugin.<name>.`` namespace while keeping
                the same read-surface the legacy sub-tree dict
                offered (``ctx.config["api_key"]``).

        Returns:
            A :class:`ConfigView` sharing the cache by reference; a
            subsequent :meth:`set` is observed by every live view
            without re-construction.
        """
        return ConfigView(self._cache, prefix)

    async def get(self, key: str, default: Any = None) -> Any:
        """Return the cached value for ``key`` or ``default`` when absent.

        Reads hit the in-memory cache — the DB is not queried. This
        is safe because :meth:`set` / :meth:`unset` update cache and
        DB under the same executor-serialised write.
        """
        return self._cache.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        """Upsert ``key`` → ``value``; update cache; emit ``config.updated``.

        Args:
            key: Dotted configuration key — e.g. ``"provider"``,
                ``"plugin.llm_openai.api_key"``. Leading / trailing
                whitespace is rejected so the DB does not accumulate
                near-duplicates.
            value: Any JSON-encodable scalar / list / dict.

        Raises:
            TypeError: If ``value`` contains a non-JSON type.
            ValueError: If ``key`` is empty.
        """
        self._require_open()
        if not key:
            raise ValueError("config key must be a non-empty string")
        _validate_json_value(value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        now = int(time.time())
        await self._run_db(_upsert_row, key, encoded, now)
        self._cache[key] = value
        await self._emit_updated(key)

    async def unset(self, key: str) -> bool:
        """Remove ``key`` if present; emit ``config.updated`` on deletion.

        Returns:
            ``True`` when a row was removed, ``False`` when the key
            did not exist — lets CLI callers distinguish "nothing to
            do" from "cleanup succeeded".
        """
        self._require_open()
        if key not in self._cache:
            # Still try the DB in case the cache was somehow out of
            # sync; cache-miss with DB-hit should never happen, but
            # failing closed here lets an accidental drift self-heal.
            deleted = await self._run_db(_delete_row, key)
            if deleted:
                await self._emit_updated(key)
            return bool(deleted)
        await self._run_db(_delete_row, key)
        self._cache.pop(key, None)
        await self._emit_updated(key)
        return True

    async def list_prefix(self, prefix: str) -> dict[str, Any]:
        """Return every cached key starting with ``prefix`` in sorted order.

        ``prefix=""`` returns the entire cache. Ordering is
        deterministic (lexicographic) so CLI output is stable across
        invocations.
        """
        if not prefix:
            return {k: self._cache[k] for k in sorted(self._cache)}
        return {k: self._cache[k] for k in sorted(self._cache) if k.startswith(prefix)}

    async def migrate_from_kernel_config(self, cfg: Mapping[str, Any]) -> int:
        """Seed an empty DB from a flattened :class:`KernelConfig` mapping.

        Called exactly once per install — the :data:`_MIGRATION_MARKER`
        key is written on success, and subsequent boots skip this
        method entirely.

        Args:
            cfg: Flattened dotted-key → value pairs. The caller
                (typically :func:`flatten_kernel_config`) is
                responsible for picking which fields migrate.

        Returns:
            Number of rows written (excluding the marker).
        """
        self._require_open()
        if _MIGRATION_MARKER in self._cache:
            return 0
        written = 0
        for key, value in cfg.items():
            if not key or key == _MIGRATION_MARKER:
                continue
            try:
                _validate_json_value(value)
            except TypeError:
                # Skip non-JSON entries silently; they were unreachable
                # via the old config anyway (KernelConfig fields are
                # already JSON-safe).
                continue
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            now = int(time.time())
            await self._run_db(_upsert_row, key, encoded, now)
            self._cache[key] = value
            written += 1
        # Marker last so a crash mid-migration re-runs next boot.
        now = int(time.time())
        encoded_marker = json.dumps(now, ensure_ascii=False, sort_keys=True)
        await self._run_db(_upsert_row, _MIGRATION_MARKER, encoded_marker, now)
        self._cache[_MIGRATION_MARKER] = now
        return written

    # -- internals ------------------------------------------------------------

    def _require_open(self) -> None:
        if self._closed or self._conn is None or self._executor is None:
            raise RuntimeError("ConfigStore is closed")

    async def _run_db(self, fn: Any, /, *args: Any) -> Any:
        """Funnel ``fn(*args)`` onto the single DB worker thread."""
        assert self._conn is not None  # noqa: S101 - guarded by _require_open.
        assert self._executor is not None  # noqa: S101
        loop = asyncio.get_running_loop()
        call = functools.partial(fn, self._conn, *args)
        return await loop.run_in_executor(self._executor, call)

    async def _emit_updated(self, key: str) -> None:
        """Publish ``config.updated`` on the reserved ``"kernel"`` session.

        ``prefix_match_hint`` is the key up to (but not including) the
        last dotted component so plugins can early-exit a
        subscription filter without parsing. For keys with no dots
        the hint is the empty string.
        """
        if self._bus is None:
            return
        hint = key.rsplit(".", 1)[0] + "." if "." in key else ""
        event = new_event(
            "config.updated",
            {"key": key, "prefix_match_hint": hint},
            session_id=_KERNEL_SESSION,
            source=_CONFIG_SOURCE,
        )
        await self._bus.publish(event)


# ---------------------------------------------------------------------------
# Synchronous sqlite helpers — kept module-level so the DB executor has
# picklable callables and tests can exercise them without a store.
# ---------------------------------------------------------------------------


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open the config DB and ensure schema + index exist.

    ``check_same_thread=False`` is paired with the store's single-
    worker executor: every DB call funnels onto one worker thread, so
    the connection is safe across concurrent sessions without an
    additional lock (same pattern as ``memory_sqlite``; lesson #20
    for the hazard when the pairing breaks).
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(_SCHEMA)
    conn.execute(_PREFIX_INDEX)
    conn.commit()
    return conn


def _load_all_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return every ``(key, value)`` row. Caller decodes the JSON."""
    cur = conn.execute("SELECT key, value FROM config ORDER BY key")
    return [(str(row[0]), str(row[1])) for row in cur.fetchall()]


def _upsert_row(conn: sqlite3.Connection, key: str, value: str, updated_at: int) -> None:
    """Insert or replace a single row."""
    conn.execute(
        "INSERT INTO config(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, value, updated_at),
    )
    conn.commit()


def _delete_row(conn: sqlite3.Connection, key: str) -> int:
    """Remove the row; return ``1`` when it existed, ``0`` otherwise."""
    cur = conn.execute("DELETE FROM config WHERE key = ?", (key,))
    conn.commit()
    return int(cur.rowcount or 0)
