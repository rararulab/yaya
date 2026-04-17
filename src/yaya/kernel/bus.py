"""Asyncio-native event bus for the yaya kernel.

The bus fans one published event out to every subscriber whose kind matches
exactly (no wildcards at 1.0 — see ``docs/dev/plugin-protocol.md``). Each
subscriber handler runs under a 30s timeout; a raising or hanging handler
is isolated — the bus synthesizes a ``plugin.error`` event naming the
failing subscriber and delivery to the other subscribers is unaffected.

**FIFO per session.** All events carrying the same ``session_id`` are
delivered in publish order. The invariant is implemented with one
:class:`asyncio.Queue` plus a single drain worker task per ``session_id``:
:meth:`publish` enqueues the event, and the worker pops events one at a
time and fans each out to the matching subscribers serially. Because a
single worker owns the session's queue, ordering is preserved even when
handlers themselves call :meth:`publish` (or ``KernelContext.emit``) on
the same session — those follow-up events append to the queue and run
after the current handler returns. No re-entry hazard.

Concurrent publishes on **different** sessions run in parallel because
each session owns its own queue + worker. When a session's queue drains,
its worker task and queue entry are released so idle sessions do not
leak.

Kernel-origin failures do not recurse: if a handler whose ``source`` is
already ``"kernel"`` fails (e.g. the error-emit path itself), the bus
logs and drops it rather than emitting another ``plugin.error``.
Synthetic ``plugin.error`` events are enqueued on the ``"kernel"``
session so they do not interleave with the originating session's FIFO.

Layering: no imports from ``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from yaya.kernel.events import Event, new_event

EventHandler = Callable[[Event], Awaitable[None]]

DEFAULT_HANDLER_TIMEOUT_S: float = 30.0
"""Per-subscriber handler timeout per the plugin failure model."""

_logger = logging.getLogger(__name__)

# Sentinel pushed into session queues by :meth:`EventBus.close` to wake the
# drain worker and make it exit cleanly. Using a module-level singleton so
# identity checks suffice.
_CLOSE_TOKEN: object = object()


@dataclass(slots=True)
class _Envelope:
    """Queue item pairing an event with its per-publish completion future.

    The future is resolved by the session's drain worker once every
    subscriber for this event has finished (or failed). :meth:`EventBus.publish`
    awaits this future so external callers observe synchronous-ish delivery
    semantics — except from within the worker itself, where awaiting would
    deadlock (see :meth:`EventBus.publish`).
    """

    event: Event
    done: asyncio.Future[None]


@dataclass(slots=True, eq=False)
class _Subscriber:
    """Internal bookkeeping for a single subscription.

    ``eq=False`` so ``list.remove`` falls back to identity comparison — two
    subscriptions with the same handler+source are distinct entries and
    unsubscribe removes exactly the instance whose :class:`Subscription`
    handle was cancelled.
    """

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
    drive it. :meth:`close` drains outstanding session workers and releases
    queue state so ``yaya serve`` can shut down cleanly.
    """

    def __init__(self, *, handler_timeout_s: float = DEFAULT_HANDLER_TIMEOUT_S) -> None:
        """Create an empty bus.

        Args:
            handler_timeout_s: Per-subscriber handler deadline; a handler that
                does not complete within this window is cancelled and treated
                as a failure.
        """
        self._subs: dict[str, list[_Subscriber]] = defaultdict(list)
        self._session_queues: dict[str, asyncio.Queue[object]] = {}
        self._session_workers: dict[str, asyncio.Task[None]] = {}
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

        When called from outside a session worker, the coroutine returns
        after every subscriber finished (or failed, or timed out) handling
        this specific event. Handlers may call :meth:`publish` (or
        ``KernelContext.emit``) on the **same** ``session_id`` while
        running — the follow-up event is enqueued and delivered after the
        current handler returns; the inner :meth:`publish` returns as soon
        as the event is enqueued (awaiting delivery would deadlock the
        session's single worker).

        Args:
            event: The envelope to deliver. Already validated by
                :func:`yaya.kernel.events.new_event`.

        Note:
            Publishing after :meth:`close` is a no-op that logs a WARNING
            naming the dropped ``event.kind``. The returned coroutine
            completes normally without delivering the event.
        """
        if self._closed:
            _logger.warning("publish on closed bus; dropping event kind=%r", event.kind)
            return

        queue = self._ensure_worker(event.session_id)
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        await queue.put(_Envelope(event, done))

        # Re-entry: if we're running inside the session's own worker, awaiting
        # the completion future would deadlock (the worker can't pick up the
        # new event until the current handler returns). Enqueue-and-return.
        worker = self._session_workers.get(event.session_id)
        if worker is not None and asyncio.current_task() is worker:
            return
        await done

    def _ensure_worker(self, session_id: str) -> asyncio.Queue[object]:
        """Return the session's queue, creating queue + worker on first use."""
        queue = self._session_queues.get(session_id)
        if queue is not None:
            return queue
        queue = asyncio.Queue()
        self._session_queues[session_id] = queue
        self._session_workers[session_id] = asyncio.create_task(
            self._drain(session_id, queue),
            name=f"yaya-bus-session:{session_id}",
        )
        return queue

    async def _drain(self, session_id: str, queue: asyncio.Queue[object]) -> None:
        """Pop and deliver events for one session until the queue is idle.

        Exits when the queue is empty (releasing the session's state) or
        when :data:`_CLOSE_TOKEN` is popped (shutdown path).
        """
        try:
            while True:
                item = await queue.get()
                if item is _CLOSE_TOKEN:
                    return
                assert isinstance(item, _Envelope)  # noqa: S101
                event = item.event
                # Snapshot so mid-delivery unsubscribes don't mutate the list.
                targets = list(self._subs.get(event.kind, ()))
                try:
                    for sub in targets:
                        await self._deliver(sub, event)
                finally:
                    if not item.done.done():
                        item.done.set_result(None)

                # If nothing else is pending, release the session's state so
                # idle sessions do not leak queue + task entries.
                if queue.empty():
                    break
        finally:
            # Only release if we're still the current worker for this session.
            # (close() clears the dicts itself; guard avoids KeyError then.)
            if self._session_queues.get(session_id) is queue:
                self._session_queues.pop(session_id, None)
                self._session_workers.pop(session_id, None)

    async def _deliver(self, sub: _Subscriber, event: Event) -> None:
        """Run one subscriber with timeout + error isolation."""
        try:
            await asyncio.wait_for(sub.handler(event), timeout=self._handler_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_handler_failure(sub, exc)

    async def _report_handler_failure(self, sub: _Subscriber, exc: Exception) -> None:
        """Enqueue a synthetic ``plugin.error`` unless this would recurse.

        The bus itself is ``source = "kernel"``. If the failing subscriber is
        already a kernel-owned handler, emitting another ``plugin.error``
        could loop, so we log and drop instead.

        The synthetic event is enqueued on the ``"kernel"`` session (not the
        originating session) so it does not interleave with the caller's
        per-session FIFO.
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
        err = new_event(
            "plugin.error",
            {"name": sub.source, "error": str(exc)},
            session_id="kernel",
            source="kernel",
        )
        if self._closed:
            return
        queue = self._ensure_worker("kernel")
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        await queue.put(_Envelope(err, done))
        # Do not await ``done`` — the kernel session may be the caller's own
        # worker (e.g. a plugin.error handler raised) and awaiting would
        # deadlock. The originating publish's completion future still
        # resolves once its own envelope finishes; the synthetic error
        # delivers asynchronously.
        if asyncio.current_task() is not self._session_workers.get("kernel"):
            await done

    # -- shutdown ---------------------------------------------------------------

    async def close(self) -> None:
        """Stop accepting publishes; drain outstanding session workers.

        Safe to call multiple times.
        """
        if self._closed and not self._session_workers:
            return
        self._closed = True
        # Snapshot before mutating so concurrent completions can't race.
        workers = list(self._session_workers.values())
        for queue in list(self._session_queues.values()):
            await queue.put(_CLOSE_TOKEN)
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._session_queues.clear()
        self._session_workers.clear()


__all__ = ["DEFAULT_HANDLER_TIMEOUT_S", "EventBus", "EventHandler", "Subscription"]
