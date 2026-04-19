"""SessionContext — multi-connection runtime wrapper over a :class:`Session`.

A :class:`~yaya.kernel.session.Session` (#32) owns the persistent
append-only tape; this module owns the **runtime** state on top of
it: which connections are currently attached, their heartbeats,
reconnect replay, and fanout of bus events to every live connection.
GOAL.md caps scope at single-process, local-first through 1.0, so
"multi-device" here means multiple connections within one yaya
process (two browser tabs, web + TUI, phone on LAN behind the user's
own reverse proxy), never cross-host sync.

Contract
--------

* :class:`Connection` is the per-client handle — a uuid, an async
  ``send_cb``, and a ``last_seen`` timestamp for heartbeat reap.
* :class:`SessionContext` wraps one :class:`Session` and a bounded
  :class:`dict` of connections (default cap 64, lesson #6). It exposes
  :meth:`attach` / :meth:`detach` / :meth:`fanout` / :meth:`heartbeat`.
* :class:`SessionManager` holds one :class:`SessionContext` per live
  ``session_id`` and subscribes to the bus so every event tagged with
  that session id fans out to every attached connection.
* The bundled web adapter plugin is the eventual consumer — this
  module ships the kernel primitive; the web integration lands in a
  follow-up (#16 / #28 etc).

Lesson recap
------------

* **Lesson #2** — lifecycle events (``session.context.*``) are
  published on ``session_id="kernel"`` so they do NOT enter the
  originating session's FIFO queue and deadlock a drain worker that
  might be blocked on :meth:`fanout`.
* **Lesson #6** — the connection registry is bounded; exceeding
  :attr:`SessionContext.max_connections` raises
  :class:`ConnectionLimitError`. The heartbeat reap loop keeps the
  registry from filling up with zombies.
* **Lesson #15** — ``since_entry`` carries reconnect correlation
  across drops; every replayed event mirrors the original ``id``.
* **Lesson #29** — exceptions from ``send_cb`` are translated to a
  quiet detach + ``session.context.detached(reason="send_failed")``
  instead of bubbling into the bus worker.
* **Lesson #31** — the reap loop is a plain :class:`asyncio.Task`;
  :meth:`SessionContext.close` cancels it cleanly on shutdown.

Reconnect replay
----------------

When a client reconnects it passes ``since_entry: int | None``. The
context takes a per-connection :class:`asyncio.Lock`, queries
:meth:`Session.entries` for ``entry.id > since_entry``, and pushes
each surviving entry as a ``session.replay.entry`` envelope via the
connection's ``send_cb``; a terminating ``session.replay.done``
follows. Live events that arrive DURING replay buffer behind the
lock and drain immediately after ``replay.done`` — no loss, no
duplicate.

Layering: depends on :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
:mod:`yaya.kernel.session`, and the Python standard library. No
imports from ``yaya.cli``, ``yaya.plugins``, or ``yaya.core``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from yaya.kernel.errors import YayaError
from yaya.kernel.events import Event, new_event
from yaya.kernel.session import Session

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from yaya.kernel.bus import EventBus, Subscription

__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_S",
    "DEFAULT_MAX_CONNECTIONS",
    "Connection",
    "ConnectionLimitError",
    "DetachReason",
    "SessionContext",
    "SessionManager",
    "install_session_manager",
]

_logger = logging.getLogger(__name__)

DEFAULT_MAX_CONNECTIONS: int = 64
"""Hard ceiling on the connection registry per session (lesson #6)."""

DEFAULT_HEARTBEAT_TIMEOUT_S: float = 60.0
"""Seconds of heartbeat silence before a connection is reaped."""

_REAP_INTERVAL_S: float = 5.0
"""How often the reap task polls for stale connections."""

_SOURCE: str = "kernel-session-context"
"""Subscription source — distinct from ``"kernel"`` so the bus recursion
guard does not swallow our ``plugin.error`` reports (lesson #2 rationale
mirrors :mod:`yaya.kernel.session_persister`)."""


DetachReason = Literal["client_close", "timeout", "shutdown", "send_failed"]
"""Closed set of reasons carried on ``session.context.detached`` payloads.

Values:

* ``client_close`` — adapter/client requested the detach.
* ``timeout`` — heartbeat reap fired.
* ``shutdown`` — :meth:`SessionContext.close` emptied the registry.
* ``send_failed`` — ``send_cb`` raised during a fanout or replay.
"""


SendCallback = Callable[[Event], Awaitable[None]]
"""Coroutine the kernel invokes once per fanout / replay entry.

The callback MUST be idempotent-on-failure: the manager catches raised
exceptions, detaches the connection, and does NOT retry. Adapters that
want at-least-once delivery layer retry internally on their transport.
"""


class ConnectionLimitError(YayaError):
    """Raised by :meth:`SessionContext.attach` when the registry is full.

    Subclass of :class:`~yaya.kernel.errors.YayaError` so callers can
    catch it alongside every other structured kernel error. The
    message names the cap so operators see the knob to raise.
    """


@dataclass(slots=True, eq=False)
class Connection:
    """One client attached to a :class:`SessionContext`.

    ``eq=False`` so identity comparison is the tie-breaker when the
    registry removes an entry — two connections with identical
    ``adapter_id`` must still detach independently.

    Attributes:
        id: Stable uuid4 hex assigned on attach. Echoed in every
            ``session.context.*`` payload so adapters correlate.
        adapter_id: Logical source (``"web"``, ``"tui"``, ``"telegram"``
            …). Purely informational today; adapters populate it from
            their own plugin name.
        send: Async callback the manager invokes per fanout / replay.
        attached_at: Unix epoch float set once on construction.
        last_seen: Updated by :meth:`SessionContext.heartbeat`; compared
            against the context's heartbeat timeout on reap.
    """

    id: str
    adapter_id: str
    send: SendCallback
    attached_at: float
    last_seen: float
    # Replay lock so live events arriving mid-replay buffer behind the
    # replay cursor instead of interleaving. Acquired by
    # :meth:`SessionContext._replay` for the full catch-up window and
    # by :meth:`SessionContext.fanout` for each live push. Public so
    # pyright does not flag the intra-module use as private access —
    # plugin code must still route writes through the manager, not
    # poke at this field directly.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionContext:
    """Multi-connection runtime wrapper around a :class:`Session`.

    The context owns a bounded dict of :class:`Connection` handles
    plus a background reap task that drops connections whose
    ``last_seen`` has not advanced within
    :attr:`heartbeat_timeout_s`. Losing a :class:`SessionContext` is
    strictly non-fatal: the :class:`Session` tape is the source of
    truth, so a reboot / reattach just rehydrates the context from
    scratch.

    Not thread-safe. All methods run on the asyncio loop that
    constructed the context (lesson #31 — the reap task cancels
    cleanly on :meth:`close`).
    """

    def __init__(
        self,
        *,
        session: Session,
        bus: EventBus,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        now: Callable[[], float] | None = None,
    ) -> None:
        """Bind the context to ``session`` / ``bus``.

        Args:
            session: The underlying :class:`Session`. Its
                ``session_id`` keys the context inside
                :class:`SessionManager`.
            bus: The running :class:`~yaya.kernel.bus.EventBus`; used
                for ``session.context.*`` lifecycle emissions.
            max_connections: Registry cap. Exceeding this in
                :meth:`attach` raises :class:`ConnectionLimitError`.
                Default :data:`DEFAULT_MAX_CONNECTIONS`.
            heartbeat_timeout_s: Seconds of silence that mark a
                connection stale. Default
                :data:`DEFAULT_HEARTBEAT_TIMEOUT_S`.
            now: Time source hook for deterministic tests. Defaults
                to :func:`time.time`.
        """
        self._session = session
        self._bus = bus
        self._max_connections = max_connections
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._now: Callable[[], float] = now or time.time
        self._connections: dict[str, Connection] = {}
        self._reap_task: asyncio.Task[None] | None = None
        self._closed: bool = False

    # -- introspection ----------------------------------------------------------

    @property
    def session(self) -> Session:
        """Underlying :class:`Session` (read-only)."""
        return self._session

    @property
    def session_id(self) -> str:
        """Convenience accessor for the wrapped session's id."""
        return self._session.session_id

    @property
    def max_connections(self) -> int:
        """Current registry cap. Immutable post-construction."""
        return self._max_connections

    @property
    def heartbeat_timeout_s(self) -> float:
        """Configured heartbeat deadline in seconds."""
        return self._heartbeat_timeout_s

    def connections(self) -> list[Connection]:
        """Return a snapshot list of currently attached connections.

        Returned list is a fresh copy — callers can iterate without
        worrying about concurrent :meth:`attach` / :meth:`detach`
        mutating the underlying dict mid-iteration.
        """
        return list(self._connections.values())

    # -- lifecycle --------------------------------------------------------------

    async def attach(
        self,
        send: SendCallback,
        *,
        adapter_id: str = "unknown",
        since_entry: int | None = None,
    ) -> Connection:
        """Register a new connection and optionally replay missed entries.

        Args:
            send: Coroutine callback invoked once per fanout /
                replay entry.
            adapter_id: Logical source label for observability.
                Not validated — the adapter picks its own id.
            since_entry: When set, the manager replays every tape
                entry whose ``id`` is strictly greater than
                ``since_entry`` to the new connection before any
                live fanout happens. ``None`` skips replay; the
                adapter is responsible for cold-start hydration
                via ``Session.entries()`` if it wants a full history.

        Returns:
            The freshly-registered :class:`Connection`.

        Raises:
            ConnectionLimitError: If the registry is at
                :attr:`max_connections`.
            RuntimeError: If the context has been :meth:`close`-d.
        """
        if self._closed:
            raise RuntimeError("SessionContext is closed")
        if len(self._connections) >= self._max_connections:
            raise ConnectionLimitError(
                f"session {self.session_id!r} is at the {self._max_connections}-connection cap; "
                "raise max_connections or reap stale clients first"
            )

        conn_id = uuid.uuid4().hex
        now = self._now()
        conn = Connection(
            id=conn_id,
            adapter_id=adapter_id,
            send=send,
            attached_at=now,
            last_seen=now,
        )
        self._connections[conn_id] = conn

        if since_entry is not None:
            # Hold the connection's per-conn lock for the full replay
            # window so a fanout landing on this conn mid-replay buffers
            # instead of interleaving (race guard; see module docstring).
            async with conn.lock:
                await self._replay(conn, since_entry)

        # Emit AFTER replay so adapters observing lifecycle see the
        # connection go "attached" once the catch-up stream is drained.
        await self._emit_lifecycle(
            "session.context.attached",
            {
                "session_id": self.session_id,
                "connection_id": conn.id,
                "adapter_id": conn.adapter_id,
            },
        )
        # Start the reap loop lazily so SessionContexts that only ever
        # see one short-lived connection do not leak a background task.
        self._ensure_reap_task()
        return conn

    async def detach(
        self,
        connection_id: str,
        *,
        reason: DetachReason = "client_close",
    ) -> None:
        """Drop a connection from the registry. Idempotent.

        Args:
            connection_id: The :attr:`Connection.id` returned from
                :meth:`attach`. Unknown ids are silently ignored so
                double-close paths do not raise.
            reason: Why the detach is happening — surfaced on the
                ``session.context.detached`` payload for
                observability.
        """
        conn = self._connections.pop(connection_id, None)
        if conn is None:
            return
        await self._emit_lifecycle(
            "session.context.detached",
            {
                "session_id": self.session_id,
                "connection_id": connection_id,
                "reason": reason,
            },
        )

    async def heartbeat(self, connection_id: str) -> bool:
        """Refresh ``last_seen`` for one connection. Idempotent.

        Args:
            connection_id: The connection to refresh.

        Returns:
            ``True`` if the connection was known and refreshed;
            ``False`` when the id is unknown (already detached, never
            attached, or typo).
        """
        conn = self._connections.get(connection_id)
        if conn is None:
            return False
        conn.last_seen = self._now()
        return True

    async def fanout(self, event: Event) -> None:
        """Push ``event`` to every attached connection.

        ``send_cb`` failures are isolated per-connection: the offender
        is detached with ``reason="send_failed"`` and the remaining
        connections still receive the event. Ordering across
        connections matches registry insertion order, which mirrors
        the bus's per-session FIFO — two adapters attached in turn
        see identical event sequences (AC-01 / AC-04 of the spec).

        The per-connection replay lock is honoured: if a connection is
        mid-replay, its ``send`` for the live event waits until
        ``replay.done`` has flushed. The wait is bounded by the
        replay's own completion because we drop the lock synchronously
        in :meth:`_replay` before returning.
        """
        if self._closed:
            return
        # Snapshot so a concurrent :meth:`detach` (from a failing send)
        # doesn't mutate the iteration target mid-loop.
        conns = list(self._connections.values())
        for conn in conns:
            # Skip connections the pop in a previous iteration already
            # removed (cascade detach when multiple sends fail).
            if conn.id not in self._connections:
                continue
            try:
                async with conn.lock:
                    await conn.send(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger.warning(
                    "connection %s (adapter=%s) send failed on event %r: %s",
                    conn.id,
                    conn.adapter_id,
                    event.kind,
                    exc,
                )
                await self.detach(conn.id, reason="send_failed")

    async def close(self) -> None:
        """Cancel the reap loop and detach every remaining connection.

        Safe to call multiple times. After close, further
        :meth:`attach` raises :class:`RuntimeError`; :meth:`detach` /
        :meth:`fanout` / :meth:`heartbeat` are no-ops.
        """
        if self._closed:
            return
        self._closed = True

        task = self._reap_task
        self._reap_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Normal shutdown path — the cancel we just requested.
                pass
            except Exception as exc:
                _logger.warning("reap task raised during close: %s", exc)

        # Emit detach events BEFORE clearing so observers see who
        # left and why. Snapshot via list() because we mutate the
        # dict inside the loop.
        for conn_id in list(self._connections.keys()):
            await self.detach(conn_id, reason="shutdown")

    # -- internals --------------------------------------------------------------

    def _ensure_reap_task(self) -> None:
        """Start the reap task once the first connection attaches.

        Idempotent. Cancels a previously-completed task reference so a
        long-running context that lost its reaper (e.g. test with a
        manual ``_reap_task.cancel()``) reestablishes one on next
        attach.
        """
        if self._reap_task is not None and not self._reap_task.done():
            return
        self._reap_task = asyncio.get_running_loop().create_task(
            self._reap_loop(),
            name=f"yaya-session-context-reap:{self.session_id}",
        )

    async def _reap_loop(self) -> None:
        """Poll every few seconds and reap connections past the timeout."""
        try:
            while not self._closed:
                await asyncio.sleep(_REAP_INTERVAL_S)
                await self._reap_once()
        except asyncio.CancelledError:
            # Normal shutdown path — close() cancels us.
            return

    async def _reap_once(self) -> None:
        """Single reap sweep — exposed for deterministic tests."""
        if self._closed:
            return
        deadline = self._now() - self._heartbeat_timeout_s
        stale: list[str] = [conn.id for conn in self._connections.values() if conn.last_seen < deadline]
        for conn_id in stale:
            await self.detach(conn_id, reason="timeout")

    async def _replay(self, conn: Connection, since_entry: int) -> int:
        """Push tape entries strictly after ``since_entry`` to ``conn``.

        Wraps each entry in a ``session.replay.entry`` envelope and
        follows up with ``session.replay.done``. The per-connection
        lock in the caller scopes this routine's critical section;
        concurrent :meth:`fanout` calls for the same conn wait until
        we return.

        Returns:
            Number of entries replayed (excludes the terminating
            ``session.replay.done`` sentinel).
        """
        entries = await self._session.entries()
        missed = [e for e in entries if e.id > since_entry]
        for tape_entry in missed:
            envelope = new_event(
                "session.replay.entry",
                {
                    "session_id": self.session_id,
                    "entry_id": tape_entry.id,
                    "kind": tape_entry.kind,
                    "payload": dict(tape_entry.payload),
                },
                session_id="kernel",
                source=_SOURCE,
            )
            try:
                await conn.send(envelope)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger.warning(
                    "replay send failed for conn %s at entry %d: %s",
                    conn.id,
                    tape_entry.id,
                    exc,
                )
                # Detach outside the lock (we're inside attach's lock).
                # Detach via pop so we never deadlock on our own lock.
                self._connections.pop(conn.id, None)
                await self._emit_lifecycle(
                    "session.context.detached",
                    {
                        "session_id": self.session_id,
                        "connection_id": conn.id,
                        "reason": "send_failed",
                    },
                )
                return len(missed)

        done = new_event(
            "session.replay.done",
            {
                "session_id": self.session_id,
                "connection_id": conn.id,
                "replayed": len(missed),
            },
            session_id="kernel",
            source=_SOURCE,
        )
        try:
            await conn.send(done)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("replay.done send failed for conn %s: %s", conn.id, exc)
            self._connections.pop(conn.id, None)
        return len(missed)

    async def _emit_lifecycle(self, kind: str, payload: dict[str, Any]) -> None:
        """Publish a ``session.context.*`` lifecycle event on the kernel bus.

        Routed on ``session_id="kernel"`` (lesson #2) so the
        originating session's drain worker — which may be executing
        :meth:`fanout` right now — does not deadlock on a follow-up
        it pushed into its own queue.
        """
        ev = new_event(kind, payload, session_id="kernel", source=_SOURCE)
        await self._bus.publish(ev)


# ---------------------------------------------------------------------------
# Manager — one SessionContext per live session_id.
# ---------------------------------------------------------------------------


class SessionManager:
    """Own one :class:`SessionContext` per live session; route bus events.

    The manager subscribes to a caller-supplied list of event kinds
    and forwards every delivery whose ``session_id`` has a live
    context to that context's :meth:`SessionContext.fanout`. Kinds
    for which no context exists are dropped silently — the bus keeps
    them intact on disk via the session persister, so a later
    :meth:`attach` with ``since_entry`` still catches up.

    Lifetime: instantiate after :class:`~yaya.kernel.bus.EventBus`;
    call :meth:`install` to wire subscriptions; call :meth:`close`
    on shutdown to drain every live context.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        store: Any,
        workspace: Any,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
    ) -> None:
        """Bind the manager to ``bus`` and a :class:`SessionStore`.

        Args:
            bus: The running kernel bus.
            store: :class:`~yaya.kernel.session.SessionStore` — typed
                as :data:`Any` to avoid a circular import of the
                session module at runtime (same pattern as
                :mod:`yaya.kernel.session_persister`).
            workspace: Workspace path passed to
                :meth:`SessionStore.open` when lazy-creating a
                context for a new session id.
            max_connections: Applied to every context the manager
                creates. Defaults to
                :data:`DEFAULT_MAX_CONNECTIONS`.
            heartbeat_timeout_s: Ditto, applied to every context.
        """
        self._bus = bus
        self._store = store
        self._workspace = workspace
        self._max_connections = max_connections
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._contexts: dict[str, SessionContext] = {}
        self._subs: list[Subscription] = []
        self._installed: bool = False

    async def install(self, kinds: list[str]) -> None:
        """Subscribe to ``kinds`` so matching events fan out on the bus.

        Idempotent — a second call is a no-op. Subscribers use
        ``source=_SOURCE`` so failures in :meth:`fanout` produce a
        regular ``plugin.error`` instead of hitting the bus recursion
        guard.
        """
        if self._installed:
            return
        self._installed = True
        for kind in kinds:
            sub = self._bus.subscribe(kind, self._on_event, source=_SOURCE)
            self._subs.append(sub)

    async def close(self) -> None:
        """Unsubscribe and close every live context. Idempotent."""
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        self._installed = False
        for ctx in list(self._contexts.values()):
            await ctx.close()
        self._contexts.clear()

    def get(self, session_id: str) -> SessionContext | None:
        """Return the live context for ``session_id`` or ``None``."""
        return self._contexts.get(session_id)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a UI-friendly view of every live context.

        Rows carry ``session_id`` + ``connection_count`` so
        ``yaya session connections`` can print them as JSON or a
        rich table without leaking internal handles.
        """
        return [
            {
                "session_id": ctx.session_id,
                "connection_count": len(ctx.connections()),
                "connections": [
                    {
                        "id": conn.id,
                        "adapter_id": conn.adapter_id,
                        "attached_at": conn.attached_at,
                        "last_seen": conn.last_seen,
                    }
                    for conn in ctx.connections()
                ],
            }
            for ctx in self._contexts.values()
        ]

    async def attach(
        self,
        session_id: str,
        send: SendCallback,
        *,
        adapter_id: str = "unknown",
        since_entry: int | None = None,
    ) -> Connection:
        """Lazy-create a :class:`SessionContext` and attach a connection.

        When a context already exists for ``session_id`` we reuse it —
        two tabs on the same session share one context so fanout is
        authoritative.
        """
        ctx = self._contexts.get(session_id)
        if ctx is None:
            ctx = await self._make_context(session_id)
            self._contexts[session_id] = ctx
        return await ctx.attach(send, adapter_id=adapter_id, since_entry=since_entry)

    async def detach(self, session_id: str, connection_id: str) -> None:
        """Forward a detach request to the matching context."""
        ctx = self._contexts.get(session_id)
        if ctx is None:
            return
        await ctx.detach(connection_id)

    async def heartbeat(self, session_id: str, connection_id: str) -> bool:
        """Forward a heartbeat to the matching context."""
        ctx = self._contexts.get(session_id)
        if ctx is None:
            return False
        return await ctx.heartbeat(connection_id)

    async def _make_context(self, session_id: str) -> SessionContext:
        """Open the :class:`Session` and wrap it in a new context."""
        session: Session = await self._store.open(self._workspace, session_id)
        return SessionContext(
            session=session,
            bus=self._bus,
            max_connections=self._max_connections,
            heartbeat_timeout_s=self._heartbeat_timeout_s,
        )

    async def _on_event(self, ev: Event) -> None:
        """Route one bus event to its session's context (if any).

        Events whose ``session_id`` is ``"kernel"`` belong to the
        kernel control plane and never fan out to user connections —
        the web adapter renders them via its own subscriptions if it
        cares. Any other session id with no live context is a no-op:
        the persister has already written the event to disk; a later
        :meth:`attach` with ``since_entry`` will replay it.
        """
        if ev.session_id == "kernel":
            return
        ctx = self._contexts.get(ev.session_id)
        if ctx is None:
            return
        await ctx.fanout(ev)


async def install_session_manager(
    *,
    bus: EventBus,
    store: Any,
    workspace: Any,
    kinds: list[str],
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
) -> SessionManager:
    """Construct + :meth:`install` a :class:`SessionManager`.

    Mirrors :func:`yaya.kernel.session_persister.install_session_persister`
    so the kernel boot path wires both with identical ergonomics.
    """
    manager = SessionManager(
        bus=bus,
        store=store,
        workspace=workspace,
        max_connections=max_connections,
        heartbeat_timeout_s=heartbeat_timeout_s,
    )
    await manager.install(kinds)
    return manager
