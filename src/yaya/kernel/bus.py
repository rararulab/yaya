"""Asyncio-native event bus for the yaya kernel.

The bus fans one published event out to every subscriber whose kind matches
exactly (no wildcards at 1.0 — see ``docs/dev/plugin-protocol.md``). Each
subscriber handler runs under a 30s timeout; a raising or hanging handler
is isolated — the bus synthesizes a ``plugin.error`` event naming the
failing subscriber and delivery to the other subscribers is unaffected.

**FIFO per session.** All events carrying the same ``session_id`` are
delivered in publish order. This is the contract adapters and the agent
loop depend on; a ``user.message.received`` must reach the strategy before
its follow-up ``llm.call.request``. The invariant is implemented with one
:class:`asyncio.Lock` per ``session_id``: :meth:`publish` acquires the
session's lock for the duration of the fan-out. Concurrent publishes on
**different** sessions run in parallel.

Kernel-origin failures do not recurse: if a handler whose ``source`` is
already ``"kernel"`` fails (e.g. the error-emit path itself), the bus
logs and drops it rather than emitting another ``plugin.error``.

Layering: no imports from ``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from yaya.kernel.events import Event, new_event

if TYPE_CHECKING:
    pass

EventHandler = Callable[[Event], Awaitable[None]]

DEFAULT_HANDLER_TIMEOUT_S: float = 30.0
"""Per-subscriber handler timeout per the plugin failure model."""

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Subscriber:
    """Internal bookkeeping for a single subscription."""

    kind: str
    handler: EventHandler
    source: str


@dataclass(slots=True)
class Subscription:
    """Handle returned by :meth:`EventBus.subscribe`.

    Holding this handle lets a caller cancel delivery to its handler.
    Unsubscribing is idempotent.
    """

    _bus: EventBus
    _sub: _Subscriber
    _active: bool = field(default=True)

    def unsubscribe(self) -> None:
        """Remove this subscription from the bus. Idempotent."""
        if not self._active:
            return
        self._active = False
        self._bus._remove(self._sub)


class EventBus:
    """In-process asyncio pub/sub bus with per-session FIFO ordering.

    Not safe to use across event loops; instantiate inside the loop that will
    drive it. :meth:`close` drains in-flight handlers and cancels anything
    still running so ``yaya serve`` can shut down cleanly.
    """

    def __init__(self, *, handler_timeout_s: float = DEFAULT_HANDLER_TIMEOUT_S) -> None:
        """Create an empty bus.

        Args:
            handler_timeout_s: Per-subscriber handler deadline; a handler that
                does not complete within this window is cancelled and treated
                as a failure.
        """
        self._subs: dict[str, list[_Subscriber]] = defaultdict(list)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._in_flight: set[asyncio.Task[None]] = set()
        self._closed: bool = False
        self._handler_timeout_s = handler_timeout_s

    # -- subscription management ------------------------------------------------

    def subscribe(
        self,
        kind: str,
        handler: EventHandler,
        *,
        source: str,
    ) -> Subscription:
        """Register ``handler`` for events matching ``kind`` exactly.

        Args:
            kind: Public or extension event kind. No wildcards at 1.0.
            handler: Async callable invoked per matching event.
            source: Logical owner (plugin name) used in ``plugin.error``
                payloads if ``handler`` raises or times out.

        Returns:
            A :class:`Subscription` whose ``unsubscribe()`` removes the handler.
        """
        sub = _Subscriber(kind=kind, handler=handler, source=source)
        self._subs[kind].append(sub)
        return Subscription(_bus=self, _sub=sub)

    def _remove(self, sub: _Subscriber) -> None:
        """Drop ``sub`` from the routing table; called by :class:`Subscription`."""
        subs = self._subs.get(sub.kind)
        if not subs:
            return
        try:
            subs.remove(sub)
        except ValueError:
            return
        if not subs:
            self._subs.pop(sub.kind, None)

    # -- publish ----------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Fan ``event`` out to every exact-kind subscriber.

        Delivery is serialized per ``session_id`` so listeners see events in
        publish order for a given session. Concurrent publishes to different
        sessions do not block each other. Handlers run under the configured
        per-subscriber timeout; failure in one handler is isolated via a
        synthetic ``plugin.error``.

        Args:
            event: The envelope to deliver. Already validated by
                :func:`yaya.kernel.events.new_event`.
        """
        if self._closed:  # pragma: no cover - guarded by callers.
            return

        lock = self._session_locks.setdefault(event.session_id, asyncio.Lock())
        async with lock:
            # Snapshot so mid-delivery unsubscribes don't mutate the list.
            targets = list(self._subs.get(event.kind, ()))
            if not targets:
                return
            await asyncio.gather(*(self._deliver(sub, event) for sub in targets))

    async def _deliver(self, sub: _Subscriber, event: Event) -> None:
        """Run one subscriber with timeout + error isolation."""
        try:
            await asyncio.wait_for(sub.handler(event), timeout=self._handler_timeout_s)
        except BaseException as exc:
            await self._report_handler_failure(sub, exc)

    async def _report_handler_failure(self, sub: _Subscriber, exc: BaseException) -> None:
        """Emit a synthetic ``plugin.error`` unless this would recurse.

        The bus itself is ``source = "kernel"``. If the failing subscriber is
        already a kernel-owned handler, emitting another ``plugin.error``
        could loop, so we log and drop instead.
        """
        if sub.source == "kernel":
            _logger.exception(
                "kernel-origin handler failed on event kind %r; dropping to avoid recursion",
                sub.kind,
                exc_info=exc,
            )
            return

        _logger.warning(
            "plugin %r raised while handling %r: %s",
            sub.source,
            sub.kind,
            exc,
        )
        try:
            err = new_event(
                "plugin.error",
                {"name": sub.source, "error": str(exc)},
                session_id="kernel",
                source="kernel",
            )
        except Exception:  # pragma: no cover - defensive; new_event cannot raise here.
            _logger.exception("failed to build plugin.error envelope")
            return

        # Deliver directly to plugin.error subscribers without re-acquiring the
        # originating session lock — the error is a kernel-session event.
        targets = list(self._subs.get("plugin.error", ()))
        if not targets:
            return
        err_lock = self._session_locks.setdefault("kernel", asyncio.Lock())
        async with err_lock:
            await asyncio.gather(*(self._deliver(t, err) for t in targets))

    # -- shutdown ---------------------------------------------------------------

    async def close(self) -> None:
        """Stop accepting publishes; cancel anything still in flight.

        Safe to call multiple times.
        """
        self._closed = True
        if not self._in_flight:
            return
        for task in list(self._in_flight):
            task.cancel()
        await asyncio.gather(*self._in_flight, return_exceptions=True)
        self._in_flight.clear()


__all__ = ["DEFAULT_HANDLER_TIMEOUT_S", "EventBus", "EventHandler", "Subscription"]
