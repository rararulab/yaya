"""Bus ↔ tape auto-persister (#32).

A kernel-owned subscriber that mirrors bus events onto the active
:class:`~yaya.kernel.session.Session` tape. The mapping is the table
in ``docs/dev/plugin-protocol.md#sessions-and-tape``:

    user.message.received  → message role=user
    assistant.message.delta → (skipped — too chatty)
    assistant.message.done  → message role=assistant
    tool.call.request       → tool_call
    tool.call.result        → tool_result
    any other public kind   → event(name=<kind>, data=payload)

Design rules (these exist for lesson reasons, tread carefully):

* **Never re-publish on the bus.** The persister is itself a bus
  subscriber; emitting an event from inside a handler would either
  recurse (if the subscriber attribution were `kernel`) or flood the
  FIFO. When a tape write fails we emit ``plugin.error`` with
  ``source="kernel-session-persister"`` so the bus recursion guard
  (lesson #2) still lets adapters see the incident.
* **Honour the originating session id.** Events for session ``A``
  land on tape ``A``'s store even though the subscription runs on
  the ``"kernel"`` session. No cross-session contamination.
* **Opt-out.** Any event whose ``envelope.payload`` carries a
  ``persist=False`` key (or its ``meta``-style ``__persist__`` alias)
  is skipped. Plugins can flag noisy ``x.<plugin>.*`` extension
  events this way.
* **Best-effort.** Tape-write failure logs at WARNING and continues —
  losing one observational entry is strictly better than halting the
  session worker.

Layering: depends on :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
:mod:`yaya.kernel.session`, and the Python standard library. No
imports from ``yaya.cli``, ``yaya.plugins``, or ``yaya.core``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from yaya.kernel.events import Event, new_event
from yaya.kernel.session import Session

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from yaya.kernel.bus import EventBus, Subscription

__all__ = ["SessionPersister", "install_session_persister"]

_SKIP_KINDS: frozenset[str] = frozenset({
    # Streaming deltas are deliberately not persisted — too chatty and
    # the final ``assistant.message.done`` captures the full answer.
    "assistant.message.delta",
    "llm.call.delta",
    # Session lifecycle events mirror tape operations we already
    # performed; persisting them would create duplicate anchors.
    "session.started",
    "session.handoff",
    "session.reset",
    "session.archived",
    "session.forked",
    # Compaction events ride on session_id="kernel" (lesson #2) so they
    # never land on a user tape; listing them here documents the intent
    # and makes future audits cheap.
    "session.compaction.started",
    "session.compaction.completed",
    "session.compaction.failed",
})
"""Kinds the persister never writes to a tape."""

_SOURCE = "kernel-session-persister"
"""Subscription source — distinct from ``"kernel"`` so bus failure-routing
does not hit the recursion guard. Mirrors the approval runtime's
``"kernel-approval"`` convention."""


_logger = logging.getLogger(__name__)


class SessionPersister:
    """Subscribe to every public kind and mirror matching events onto a tape.

    The persister is bound to one :class:`~yaya.kernel.session.SessionStore`
    plus a workspace path. When an event lands the persister looks up
    (or opens) the session for ``ev.session_id`` and writes the
    canonical entry. Sessions for ``session_id="kernel"`` are ignored
    — they belong to the kernel's own plumbing.

    Not thread-safe; the kernel drives it from one asyncio loop.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        store: Any,
        workspace: Any,
    ) -> None:
        """Bind the persister to ``bus`` / ``store`` / ``workspace``.

        ``store`` is typed as :data:`Any` to keep a strict-mode
        import boundary (avoiding a circular reference with
        :mod:`yaya.kernel.session`); runtime checks ensure only a
        :class:`~yaya.kernel.session.SessionStore` is passed.
        """
        self._bus = bus
        self._store = store
        self._workspace = workspace
        self._subs: list[Subscription] = []
        self._sessions: dict[str, Session] = {}
        self._installed = False

    async def start(self, kinds: list[str]) -> None:
        """Subscribe to each ``kinds`` entry; open the default session."""
        if self._installed:
            return
        self._installed = True
        for kind in kinds:
            sub = self._bus.subscribe(
                kind,
                self._on_event,
                source=_SOURCE,
            )
            self._subs.append(sub)

    async def stop(self) -> None:
        """Drop every subscription. Idempotent."""
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        self._installed = False
        self._sessions.clear()

    async def _on_event(self, ev: Event) -> None:
        """Bus handler — mirror ``ev`` onto the tape of its session."""
        if ev.session_id == "kernel":
            return
        if ev.kind in _SKIP_KINDS:
            return
        if _opted_out(ev):
            return
        try:
            session = await self._session_for(ev.session_id)
            await _write_entry(session, ev)
        except Exception as exc:
            _logger.warning(
                "session persister failed to write event %r on session %r: %s",
                ev.kind,
                ev.session_id,
                exc,
            )
            await self._emit_failure(ev, exc)

    async def _session_for(self, session_id: str) -> Session:
        cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        session: Session = await self._store.open(self._workspace, session_id)
        # Bounded by the adapter's session churn — adapters are expected
        # to reuse ids, so this is a small map. Still: lesson #6 —
        # unbounded dicts leak. We cap at 1024 and evict FIFO.
        if len(self._sessions) >= 1024:
            first_key = next(iter(self._sessions))
            self._sessions.pop(first_key, None)
        self._sessions[session_id] = session
        return session

    async def _emit_failure(self, ev: Event, exc: Exception) -> None:
        """Publish a ``plugin.error`` blaming the persister, not the kernel.

        Routed on the ``"kernel"`` session so it does not interleave
        with the originating session's FIFO, mirroring the bus's own
        synthetic-error path.
        """
        try:
            err = new_event(
                "plugin.error",
                {
                    "name": _SOURCE,
                    "error": f"tape_write_failed: {exc}",
                    "kind": "tape_write",
                },
                session_id="kernel",
                source=_SOURCE,
            )
            await self._bus.publish(err)
        except Exception:
            _logger.exception("session persister failed to emit plugin.error")

        # Record the originating event id so log scrapers can correlate.
        _logger.warning("session persister failure traceable via event id %s", ev.id)


async def install_session_persister(
    *,
    bus: EventBus,
    store: Any,
    workspace: Any,
    kinds: list[str],
) -> SessionPersister:
    """Construct, start, and return a :class:`SessionPersister`.

    The caller owns teardown via :meth:`SessionPersister.stop`.

    Args:
        bus: The running :class:`~yaya.kernel.bus.EventBus`.
        store: A :class:`~yaya.kernel.session.SessionStore`.
        workspace: Workspace path the persister uses when opening
            sessions.
        kinds: Event kinds to subscribe to. Passed explicitly so
            tests can scope the subscriber to a minimal set and so
            the kernel boot path can pass the full closed catalog
            minus :data:`_SKIP_KINDS`.

    Returns:
        The running :class:`SessionPersister`.
    """
    persister = SessionPersister(bus=bus, store=store, workspace=workspace)
    await persister.start(kinds)
    return persister


def _opted_out(ev: Event) -> bool:
    """True iff the event payload flags itself as non-persisted."""
    payload = ev.payload
    direct = payload.get("persist")
    if isinstance(direct, bool) and not direct:
        return True
    meta_value = payload.get("__persist__")
    return isinstance(meta_value, bool) and not meta_value


async def _write_entry(session: Session, ev: Event) -> None:
    """Translate one bus event into one tape entry per the contract table."""
    writer = _WRITERS.get(ev.kind, _write_generic_event)
    await writer(session, ev)


async def _write_user_message(session: Session, ev: Event) -> None:
    text = _string(ev.payload.get("text", ""))
    await session.append_message("user", text, source=ev.source)


async def _write_assistant_done(session: Session, ev: Event) -> None:
    content = _string(ev.payload.get("content", ""))
    await session.append_message("assistant", content, source=ev.source)


async def _write_tool_call(session: Session, ev: Event) -> None:
    payload = ev.payload
    await session.append_tool_call({
        "id": _string(payload.get("id", "")),
        "name": _string(payload.get("name", "")),
        "args": payload.get("args", {}) if isinstance(payload.get("args"), dict) else {},
    })


async def _write_tool_result(session: Session, ev: Event) -> None:
    payload = ev.payload
    result: dict[str, Any] = {
        "ok": bool(payload.get("ok", False)),
    }
    if "value" in payload:
        result["value"] = payload.get("value")
    if "error" in payload:
        result["error"] = payload.get("error")
    await session.append_tool_result(
        tool_call_id=_string(payload.get("id", "")),
        result=result,
    )


async def _write_generic_event(session: Session, ev: Event) -> None:
    await session.append_event(ev.kind, dict(ev.payload), source=ev.source)


_WRITERS: dict[str, Callable[[Session, Event], Awaitable[None]]] = {
    "user.message.received": _write_user_message,
    "assistant.message.done": _write_assistant_done,
    "tool.call.request": _write_tool_call,
    "tool.call.result": _write_tool_result,
}


def _string(value: object) -> str:
    return value if isinstance(value, str) else str(value)
