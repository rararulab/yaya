"""Session + tape store — the yaya kernel's canonical session state (#32).

A **session** is an append-only event log ("tape") plus a handful of
semantic helpers on top. Every bus event can be persisted as a
:class:`~republic.TapeEntry`; the LLM context is a derived view over
the tape (see :mod:`yaya.kernel.tape_context`), never a mutable
history list the kernel has to keep in memory. Fork, reset, and
compaction are cheap because they never mutate past entries.

This module is kernel-owned. It wraps ``republic``'s tape primitives
(``AsyncTapeManager``, ``InMemoryTapeStore``, ``TapeContext``,
``TapeEntry``, ``TapeQuery``) without re-implementing them. The one
non-trivial bit is :class:`_ForkOverlayStore`, a lightweight overlay
that lets :meth:`Session.fork` give a child session "parent entries
+ own appends" semantics without touching the parent's storage.

Layering: depends on :mod:`republic`, :mod:`yaya.kernel.tape_context`,
and the Python standard library. No imports from ``yaya.cli``,
``yaya.plugins``, or ``yaya.core``.

Security note on tape naming
----------------------------
``tape_name_for`` uses ``hashlib.md5(..., usedforsecurity=False)`` to
derive a short, deterministic identifier from workspace path + session
id. The identifier is a collision-tolerant routing key, never a
security primitive: it only needs "different workspace / different
session ⇒ different name" with overwhelming probability. MD5 is fine
for that, and ``usedforsecurity=False`` documents the intent for
auditors and FIPS environments.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict
from republic import TapeContext, TapeEntry
from republic.tape import (
    AsyncTapeManager,
    AsyncTapeStore,
    AsyncTapeStoreAdapter,
    InMemoryTapeStore,
    TapeQuery,
    TapeStore,
)

from yaya.kernel.tape_context import default_tape_context, select_messages

__all__ = [
    "MemoryTapeStore",
    "Session",
    "SessionInfo",
    "SessionStore",
    "default_session_dir",
    "tape_name_for",
]


_DEFAULT_OWNER: str = "human"
"""Default ``owner`` field on the bootstrap ``session/start`` anchor."""


def default_session_dir() -> Path:
    """Return the XDG-resolved directory holding session tape files.

    Honours ``YAYA_STATE_DIR`` first (test / CI override), then
    ``XDG_STATE_HOME`` per the spec, falling back to
    ``~/.local/state`` per the XDG defaults.
    """
    explicit = os.environ.get("YAYA_STATE_DIR")
    if explicit:
        return Path(explicit) / "tapes"
    raw = os.environ.get("XDG_STATE_HOME") or ""
    base = Path(raw) if raw else Path.home() / ".local" / "state"
    return base / "yaya" / "tapes"


def tape_name_for(workspace: Path, session_id: str) -> str:
    """Derive the stable tape name for ``(workspace, session_id)``.

    The rule is the bub-compatible ``md5(workspace_abspath)[:16] +
    "__" + md5(session_id)[:16]`` — see :func:`default_session_dir`
    for the security rationale. Re-exported so CLI tooling and tests
    can compute the same identifier without touching a SessionStore.

    Args:
        workspace: Any path; resolved to absolute before hashing.
        session_id: Opaque session identifier supplied by the caller.

    Returns:
        A kebab-free ``<ws16>__<sid16>`` string safe to use as a file
        stem, a bus routing key, and a log field.
    """
    ws_hex = hashlib.md5(
        str(workspace.resolve()).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    sid_hex = hashlib.md5(
        session_id.encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return f"{ws_hex}__{sid_hex}"


class SessionInfo(BaseModel):
    """Lightweight snapshot of a :class:`Session` for UI / CLI rendering.

    Immutable by convention; callers must not mutate after
    construction. The fields match the agent-friendly CLI contract so
    ``yaya session list --json`` can dump them verbatim.

    Attributes:
        session_id: Logical session identifier supplied by the caller.
        tape_name: Derived tape name — see :func:`tape_name_for`.
        created_at: ISO-8601 UTC timestamp for first-seen or reopen.
        entry_count: Total entries visible on the tape at snapshot time.
        last_anchor: Name of the most recent ``anchor`` entry, or
            ``None`` when the tape has none yet.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    tape_name: str
    created_at: str
    entry_count: int
    last_anchor: str | None


# ---------------------------------------------------------------------------
# Store implementations.
# ---------------------------------------------------------------------------


#: In-memory tape store — alias for :class:`~republic.tape.InMemoryTapeStore`.
#:
#: Re-exported under a yaya-native name so tests and ``yaya hello``
#: keep a stable import even if upstream renames the implementation
#: class. Kept as a module-level alias rather than a subclass because
#: :class:`~republic.tape.InMemoryTapeStore` transits through mypy as
#: ``Any`` (republic ships without ``py.typed``) and a subclass would
#: trip ``disallow_any_unimported`` even under our narrow override.
MemoryTapeStore = InMemoryTapeStore


class _ForkOverlayStore:
    """Overlay store: parent entries are read-only; writes land in an in-memory child.

    Implements the :class:`~republic.tape.AsyncTapeStore` duck type
    (``list_tapes`` / ``reset`` / ``fetch_all`` / ``append``) so
    ``AsyncTapeManager`` treats it like any other backend. Parent is
    queried via an async adapter when needed; the child is an
    in-memory store owned by this overlay.

    Fork semantics:

    * ``append`` — always writes to the child.
    * ``fetch_all`` — parent entries first, then child entries
      (child entries get synthetic ids stamped from ``parent_len + i``
      so the overall stream is ordered).
    * ``reset`` — clears the child only. The parent is immutable from
      a fork's perspective; lesson-learned: a fork that could wipe
      its parent is a footgun.

    Not thread-safe. Async-friendly because every method matches the
    AsyncTapeStore signature.
    """

    def __init__(self, parent: Any) -> None:
        """Wrap ``parent`` with an empty in-memory child overlay.

        ``parent`` is accepted as :data:`Any` because republic's
        :class:`~republic.tape.AsyncTapeStore` and :class:`~republic.tape.TapeStore`
        transit as untyped through the static checker; we preserve
        runtime discrimination via :func:`_is_async_store`.
        """
        self._parent: Any = parent if _is_async_store(parent) else AsyncTapeStoreAdapter(parent)
        self._child = InMemoryTapeStore()

    async def list_tapes(self) -> list[str]:
        """Return the union of parent + child tape names."""
        parent_tapes = list(await self._parent.list_tapes())
        child_tapes = list(self._child.list_tapes())
        return sorted(set(parent_tapes) | set(child_tapes))

    async def reset(self, tape: str) -> None:
        """Clear child entries for ``tape``; parent is untouched."""
        self._child.reset(tape)

    async def fetch_all(self, query: Any) -> Iterable[TapeEntry]:
        """Return parent entries followed by child entries for ``query.tape``."""
        parent_entries: list[TapeEntry] = []
        try:
            parent_entries = list(await self._parent.fetch_all(query))
        except Exception:
            parent_entries = []
        # Re-run the query against the child separately. The in-memory store
        # implements a matching ``fetch_all`` via ``InMemoryQueryMixin`` on a
        # fresh query object so limits / filters are honoured.
        child_query: Any = TapeQuery(tape=query.tape, store=self._child)
        child_store: Any = self._child
        raw_child: Iterable[TapeEntry] = child_store.fetch_all(child_query)
        child_entries: list[TapeEntry] = list(raw_child)
        return [*parent_entries, *child_entries]

    async def append(self, tape: str, entry: TapeEntry) -> None:
        """Write ``entry`` to the child; parent is never mutated."""
        self._child.append(tape, entry)


def _is_async_store(store: object) -> bool:
    """Runtime AsyncTapeStore detector — inspects the callable, not a call.

    Avoids spawning a coroutine we would then fail to await. Checks
    whether ``list_tapes`` is declared ``async`` via
    :func:`asyncio.iscoroutinefunction` (also true for
    ``AsyncTapeStoreAdapter`` which wraps a sync store).
    """
    method = getattr(store, "list_tapes", None)
    if method is None:
        return False
    return inspect.iscoroutinefunction(method)


# ---------------------------------------------------------------------------
# File-backed store (jsonl).
# ---------------------------------------------------------------------------


class _FileTapeStore:
    """Lightweight jsonl-per-tape store.

    One ``<tape_name>.jsonl`` file under ``directory`` per live tape.
    Append-only. ``reset`` deletes the file. ``fetch_all`` streams the
    file, parses each line, and applies the query's kind / anchor /
    limit filters in memory (tapes are bounded by session size; real
    compaction is handled via the ``handoff`` anchor path).

    Not thread-safe by design — the kernel writes tapes from one
    asyncio loop. File locking is out of scope for 0.1.
    """

    def __init__(self, directory: Path) -> None:
        """Ensure ``directory`` exists and remember it for later appends."""
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        # Per-tape next-id cache. Populated lazily on first ``append`` by
        # counting lines in the jsonl file; invalidated on ``reset``. Keeps
        # ``append`` O(1) after the first write instead of O(n) per call
        # (and O(n²) for n appends, which is what the uncached implementation
        # degenerated to).
        self._next_id: dict[str, int] = {}

    def _path(self, tape: str) -> Path:
        return self._directory / f"{tape}.jsonl"

    def list_tapes(self) -> list[str]:
        """Return stems of every ``*.jsonl`` file in the directory."""
        return sorted(p.stem for p in self._directory.glob("*.jsonl"))

    def reset(self, tape: str) -> None:
        """Delete the tape's jsonl file if present. Idempotent."""
        path = self._path(tape)
        if path.exists():
            path.unlink()
        # Drop the cached next-id so a fresh tape starts at 1 again.
        self._next_id.pop(tape, None)

    def fetch_all(self, query: Any) -> Iterable[TapeEntry]:
        """Return all entries on ``query.tape`` matching the query filters."""
        entries = self._load(query.tape)
        filtered = _apply_query(entries, query)
        return filtered

    def append(self, tape: str, entry: TapeEntry) -> None:
        """Append ``entry`` to the tape's jsonl file."""
        next_id = self._next_id.get(tape)
        if next_id is None:
            # Cold path: count existing entries once, then cache.
            next_id = len(self._load(tape)) + 1
        stored = TapeEntry(next_id, entry.kind, dict(entry.payload), dict(entry.meta), entry.date)
        with self._path(tape).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(stored), ensure_ascii=False) + "\n")
        self._next_id[tape] = next_id + 1

    def tail(self, tape: str, n: int) -> list[TapeEntry]:
        """Return the last ``n`` entries on ``tape`` using bounded memory.

        Streams the jsonl file line-by-line into a
        :class:`collections.deque` with ``maxlen=n`` so memory usage is
        bounded by ``n`` regardless of tape size. Time is O(file-lines)
        which is acceptable for the CLI ``--tail`` path; a seek-backwards
        implementation is premature optimisation.
        """
        if n <= 0:
            return []
        path = self._path(tape)
        if not path.exists():
            return []
        window: deque[TapeEntry] = deque(maxlen=n)
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed = _entry_from_dict(payload)
                if parsed is not None:
                    window.append(parsed)
        return list(window)

    def _load(self, tape: str) -> list[TapeEntry]:
        path = self._path(tape)
        if not path.exists():
            return []
        out: list[TapeEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry = _entry_from_dict(payload)
                if entry is not None:
                    out.append(entry)
        return out


def _entry_from_dict(data: object) -> TapeEntry | None:
    if not isinstance(data, dict):
        return None
    d = cast("dict[str, Any]", data)
    entry_id = d.get("id")
    kind = d.get("kind")
    payload = d.get("payload")
    meta_value: Any = d.get("meta") or {}
    date = d.get("date") or datetime.now(UTC).isoformat()
    if not isinstance(entry_id, int) or not isinstance(kind, str) or not isinstance(payload, dict):
        return None
    meta_dict: dict[str, Any] = dict(cast("dict[str, Any]", meta_value)) if isinstance(meta_value, dict) else {}
    return TapeEntry(
        entry_id,
        kind,
        dict(cast("dict[str, Any]", payload)),
        meta_dict,
        str(date),
    )


def _apply_query(
    entries: list[TapeEntry],
    query: Any,
) -> list[TapeEntry]:
    """Apply kinds / after_last / after_anchor / limit filters in order.

    Republic exposes its ``TapeQuery`` filter state via ``_`` prefixed
    dataclass fields rather than a public API. Accepting ``query`` as
    :data:`Any` here avoids every call-site paying a
    ``reportPrivateUsage`` fine that cannot be resolved without a
    matching republic accessor. The runtime shape is still pinned by
    the ``republic.TapeQuery`` dataclass.
    """
    filtered = list(entries)
    kinds = query._kinds
    if kinds:
        filtered = [e for e in filtered if e.kind in kinds]
    after_last = query._after_last
    after_anchor = query._after_anchor
    if after_last:
        pruned: list[TapeEntry] = []
        for e in filtered:
            if e.kind == "anchor":
                pruned = []
                continue
            pruned.append(e)
        filtered = pruned
    elif after_anchor is not None:
        pruned = []
        seen_anchor = False
        for e in filtered:
            if e.kind == "anchor" and str(e.payload.get("name")) == after_anchor:
                seen_anchor = True
                pruned = []
                continue
            if seen_anchor:
                pruned.append(e)
        filtered = pruned
    limit = query._limit
    if limit is not None and limit >= 0:
        filtered = filtered[-limit:] if limit else []
    return filtered


# ---------------------------------------------------------------------------
# SessionStore / Session.
# ---------------------------------------------------------------------------


class Session:
    """A workspace-scoped append-only conversation log.

    A Session wraps one tape inside an :class:`AsyncTapeManager`.
    Writes go through helpers that stamp the right
    :class:`~republic.TapeEntry` factory (``message`` /
    ``tool_call`` / ``tool_result`` / ``event`` / ``anchor``). Reads
    come back via :meth:`context` (default selection → LLM messages)
    or :meth:`entries` (raw tape entries) for tooling that wants the
    full stream.

    Not thread-safe — the kernel drives sessions from one asyncio
    loop. Concurrent ``append_*`` calls from the same loop serialise
    naturally on the underlying manager.
    """

    def __init__(
        self,
        *,
        session_id: str,
        tape_name: str,
        manager: AsyncTapeManager,
        workspace: Path,
        created_at: str,
        archive_root: Path | None = None,
    ) -> None:
        """Bind the session to its tape. Use :meth:`SessionStore.open` for construction."""
        self._session_id = session_id
        self._tape_name = tape_name
        self._manager = manager
        self._workspace = workspace
        self._created_at = created_at
        self._archive_root = archive_root or (default_session_dir() / ".archive")

    # -- read-only identity -----------------------------------------------------

    @property
    def session_id(self) -> str:
        """Caller-supplied logical id; mirrors ``SessionInfo.session_id``."""
        return self._session_id

    @property
    def tape_name(self) -> str:
        """Derived tape id; mirrors :func:`tape_name_for`."""
        return self._tape_name

    @property
    def workspace(self) -> Path:
        """Absolute workspace path the tape is scoped to."""
        return self._workspace

    @property
    def manager(self) -> AsyncTapeManager:
        """Underlying tape manager.

        Exposed for kernel code and advanced adapters that need
        ``TapeQuery`` composition. Plugin code should prefer the
        higher-level helpers on this class.
        """
        return self._manager

    # -- append helpers ---------------------------------------------------------

    async def handoff(self, name: str, state: dict[str, Any]) -> None:
        """Append an ``anchor`` marker with ``name`` + ``state`` to the tape.

        Anchors partition the tape. ``TapeContext.build_query`` uses
        them to scope context queries (e.g. "since session start" or
        "since compaction checkpoint"). Semantically equivalent to
        bub's ``TapeService.handoff``; we re-expose it here so plugin
        code does not need to import ``republic``.
        """
        await self._manager.handoff(self._tape_name, name, state=dict(state))

    async def append_event(self, name: str, payload: dict[str, Any], **meta: Any) -> None:
        """Append a generic ``event`` entry.

        The auto-persister uses this for every bus event kind that
        does not have a dedicated tape shape (``user.message.*`` and
        ``tool.*`` get canonical factories; everything else ends up
        here).

        Args:
            name: Event kind or observational name.
            payload: Arbitrary data payload. Serialised as-is.
            **meta: Additional ``TapeEntry.meta`` hints, e.g.
                ``include_in_context=True`` to expose the event to
                :func:`yaya.kernel.tape_context.select_messages`.
        """
        await self._manager.append_entry(
            self._tape_name,
            TapeEntry.event(name=name, data=dict(payload), **meta),
        )

    async def append_message(
        self,
        role: Literal["user", "assistant", "system"],
        content: str,
        **meta: Any,
    ) -> None:
        """Append a ``message`` entry with the given role / content."""
        await self._manager.append_entry(
            self._tape_name,
            TapeEntry.message({"role": role, "content": content}, **meta),
        )

    async def append_tool_call(self, tool_call: dict[str, Any]) -> None:
        """Append a single-call ``tool_call`` entry.

        The tape's ``tool_call`` factory takes a **list** of call
        dicts so the selector can emit them atomically on one
        assistant message; we accept a single dict for the common
        case and wrap it here.
        """
        await self._manager.append_entry(
            self._tape_name,
            TapeEntry.tool_call([dict(tool_call)]),
        )

    async def append_tool_result(
        self,
        tool_call_id: str,
        result: dict[str, Any],
    ) -> None:
        """Append a ``tool_result`` entry correlated by ``tool_call_id``."""
        await self._manager.append_entry(
            self._tape_name,
            TapeEntry.tool_result([{"tool_call_id": tool_call_id, "result": dict(result)}]),
        )

    # -- read / derive ----------------------------------------------------------

    async def entries(self) -> list[TapeEntry]:
        """Return every entry currently on the tape."""
        raw: Iterable[TapeEntry] = await self._manager.query_tape(self._tape_name).all()
        return list(raw)

    async def tail(self, n: int) -> list[TapeEntry]:
        """Return the last ``n`` entries using the cheapest path available.

        For file-backed tapes this streams the jsonl file into a bounded
        :class:`collections.deque` — memory is O(n) regardless of tape
        size. In-memory stores (memory, fork overlay) already hold
        every entry in RAM; we slice ``entries()`` directly there
        because the extra machinery buys nothing.

        Args:
            n: Maximum number of trailing entries to return. ``n <= 0``
                returns an empty list.

        Returns:
            Up to ``n`` entries, preserving tape order.
        """
        if n <= 0:
            return []
        file_store = self._file_store()
        if file_store is not None:
            return file_store.tail(self._tape_name, n)
        raw: Iterable[TapeEntry] = await self._manager.query_tape(self._tape_name).all()
        entries = list(raw)
        return entries[-n:]

    def _file_store(self) -> _FileTapeStore | None:
        """Return the underlying :class:`_FileTapeStore` when present.

        The store is reached through the manager's private ``_tape_store``
        attribute (republic exposes no public accessor) and, when that
        attribute is an :class:`AsyncTapeStoreAdapter`, through the
        adapter's wrapped sync store. Returns ``None`` for memory and
        fork-overlay paths — those do not benefit from streaming tail.
        """
        store: Any = self._manager._tape_store  # pyright: ignore[reportPrivateUsage]
        if isinstance(store, _FileTapeStore):
            return store
        wrapped = getattr(store, "_store", None)
        if isinstance(wrapped, _FileTapeStore):
            return wrapped
        return None

    async def context(
        self,
        selection: TapeContext | None = None,
    ) -> list[dict[str, Any]]:
        """Project tape entries onto an LLM message list.

        Args:
            selection: Override the default :class:`TapeContext`. When
                ``None`` (the default) the yaya-standard
                :func:`default_tape_context` applies.

        Returns:
            A list of ``{"role", "content", ...}`` dicts suitable to
            hand to any LLM provider plugin.
        """
        chosen = selection or default_tape_context()
        entries = await self.entries()
        selector: Any = chosen.select or select_messages
        result: list[dict[str, Any]] = list(selector(entries, chosen))
        return result

    async def info(self) -> SessionInfo:
        """Return a :class:`SessionInfo` snapshot of the current tape state."""
        entries = await self.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        last_anchor = str(anchors[-1].payload.get("name")) if anchors else None
        return SessionInfo(
            session_id=self._session_id,
            tape_name=self._tape_name,
            created_at=self._created_at,
            entry_count=len(entries),
            last_anchor=last_anchor,
        )

    # -- mutate-as-rewrite ------------------------------------------------------

    async def reset(self, *, archive: bool = True) -> Path | None:
        """Clear the tape; optionally archive it first.

        Args:
            archive: When True (default), dump every current entry to
                a timestamped file under ``tapes/.archive/`` before
                clearing. When False, entries are dropped on the
                floor — appropriate only for tests or explicit
                "nuke history" flows.

        Returns:
            The archive path when ``archive`` was True, else ``None``.
        """
        archive_path: Path | None = None
        if archive:
            archive_path = await _archive_tape(
                manager=self._manager,
                tape_name=self._tape_name,
                archive_root=self._archive_root,
            )
        await self._manager.reset_tape(self._tape_name)
        # After reset, re-seed a session/start anchor so downstream context
        # queries (last_anchor) always find a stable boundary.
        await self.handoff(
            "session/start",
            state={
                "owner": _DEFAULT_OWNER,
                "workspace": str(self._workspace),
                **({"archived": str(archive_path)} if archive_path is not None else {}),
            },
        )
        return archive_path

    def fork(self, child_id: str) -> Session:
        """Return a child Session that overlays this one's tape.

        The child has its own in-memory append stream; reads see
        parent entries followed by child entries. Writes never
        mutate the parent. The child is local to the process; it is
        NOT persisted via :class:`SessionStore.open` — callers that
        need a durable child should open a new session with a
        different id.

        Args:
            child_id: Logical id for the new session (stamped onto
                ``SessionInfo`` only; the tape name is derived from
                the parent's tape so the overlay can find it).
        """
        parent_store: Any = self._manager._tape_store  # pyright: ignore[reportPrivateUsage]
        overlay = _ForkOverlayStore(parent_store)
        child_manager = AsyncTapeManager(store=overlay)
        return Session(
            session_id=child_id,
            tape_name=self._tape_name,
            manager=child_manager,
            workspace=self._workspace,
            created_at=datetime.now(UTC).isoformat(),
            archive_root=self._archive_root,
        )


class SessionStore:
    """Open / list / archive sessions for a workspace.

    The store owns one :class:`AsyncTapeManager` (so tape identity is
    stable across opens) plus a directory root for archive dumps.
    Bookkeeping is deliberately minimal — republic does the heavy
    lifting.
    """

    def __init__(
        self,
        *,
        store: AsyncTapeStore | TapeStore | None = None,
        tapes_dir: Path | None = None,
    ) -> None:
        """Configure the backing store.

        Args:
            store: Pre-built :class:`AsyncTapeStore` / :class:`TapeStore`.
                When ``None`` (default) the file-backed
                :class:`_FileTapeStore` under :func:`default_session_dir`
                is used. Tests pass :class:`MemoryTapeStore` here.
            tapes_dir: Directory for tape files and the ``.archive/``
                subdirectory. Defaults to :func:`default_session_dir`.
        """
        self._tapes_dir = tapes_dir or default_session_dir()
        if store is None:
            sync_store: TapeStore = _FileTapeStore(self._tapes_dir)
            store = AsyncTapeStoreAdapter(sync_store)
        if not _is_async_store(store):
            store = AsyncTapeStoreAdapter(cast(TapeStore, store))
        self._store = cast("AsyncTapeStore", store)
        self._manager = AsyncTapeManager(store=self._store)
        self._archive_root = self._tapes_dir / ".archive"
        self._closed = False
        # Remember the seed timestamp so info() returns a stable value
        # across reopens inside one process.
        self._created_at: dict[str, str] = {}

    @property
    def tapes_dir(self) -> Path:
        """Root directory for this store's jsonl files."""
        return self._tapes_dir

    async def open(self, workspace: Path, session_id: str) -> Session:
        """Open (or resume) a :class:`Session` for ``(workspace, session_id)``.

        On first open the tape is seeded with a
        ``anchor(name="session/start")`` so :meth:`Session.context`
        selectors always find a boundary.
        """
        if self._closed:
            raise RuntimeError("SessionStore is closed")
        tape_name = tape_name_for(workspace, session_id)
        created_at = self._created_at.setdefault(tape_name, datetime.now(UTC).isoformat())
        session = Session(
            session_id=session_id,
            tape_name=tape_name,
            manager=self._manager,
            workspace=workspace,
            created_at=created_at,
            archive_root=self._archive_root,
        )
        await _ensure_bootstrap_anchor(self._manager, tape_name, workspace)
        return session

    async def list_sessions(self, workspace: Path) -> list[SessionInfo]:
        """Return a :class:`SessionInfo` row for every tape in ``workspace``.

        Filters by the workspace-hash prefix in the tape name so
        cross-workspace entries stay invisible (issue's AC-04 rule).
        """
        if self._closed:
            return []
        ws_prefix = hashlib.md5(
            str(workspace.resolve()).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]
        tapes = await self._manager.list_tapes()
        infos: list[SessionInfo] = []
        for name in tapes:
            if not name.startswith(f"{ws_prefix}__"):
                continue
            entries = list(await self._manager.query_tape(name).all())
            anchors = [e for e in entries if e.kind == "anchor"]
            last_anchor = str(anchors[-1].payload.get("name")) if anchors else None
            created_at = self._created_at.setdefault(name, datetime.now(UTC).isoformat())
            # Session id is not recoverable from the hashed tape name;
            # surface the tape-name suffix so operators can still
            # correlate rows.
            sid_suffix = name.split("__", 1)[-1]
            infos.append(
                SessionInfo(
                    session_id=sid_suffix,
                    tape_name=name,
                    created_at=created_at,
                    entry_count=len(entries),
                    last_anchor=last_anchor,
                )
            )
        return infos

    async def archive(self, session_id: str, *, workspace: Path | None = None) -> Path:
        """Archive the tape for ``session_id`` and return the archive path.

        When ``workspace`` is omitted, uses the current working
        directory — matches the ``yaya session`` CLI semantics.
        """
        if self._closed:
            raise RuntimeError("SessionStore is closed")
        ws = workspace or Path.cwd()
        tape_name = tape_name_for(ws, session_id)
        return await _archive_tape(
            manager=self._manager,
            tape_name=tape_name,
            archive_root=self._archive_root,
        )

    async def close(self) -> None:
        """Idempotent teardown. Keeps state dir; drops in-memory caches."""
        self._closed = True
        self._created_at.clear()


# ---------------------------------------------------------------------------
# Module-level helpers.
# ---------------------------------------------------------------------------


async def _ensure_bootstrap_anchor(
    manager: AsyncTapeManager,
    tape_name: str,
    workspace: Path,
) -> None:
    """Emit the ``session/start`` anchor once per tape."""
    anchors = list(await manager.query_tape(tape_name).kinds("anchor").all())
    if anchors:
        return
    await manager.handoff(
        tape_name,
        "session/start",
        state={"owner": _DEFAULT_OWNER, "workspace": str(workspace)},
    )


async def _archive_tape(
    manager: AsyncTapeManager,
    tape_name: str,
    archive_root: Path,
) -> Path:
    """Dump every entry on ``tape_name`` as jsonl to the archive directory."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_path = archive_root / f"{tape_name}.jsonl.{stamp}.bak"
    entries = list(await manager.query_tape(tape_name).all())
    with archive_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")
    return archive_path
