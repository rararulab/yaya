"""Kernel-owned fixed agent loop.

The loop is the scheduler. Per ``docs/dev/plugin-protocol.md#agent-loop-kernel-owned``,
one ``user.message.received`` event triggers exactly one turn with the fixed
ordering:

    user.message.received
      → strategy.decide.request   → strategy.decide.response
        → memory.query            → memory.result        (if requested)
        → llm.call.request        → llm.call.response
          → tool.call.request     → tool.call.result     (repeat per tool)
        → assistant.message.done
      → memory.write              (if requested by strategy)

Strategy plugins decide *content* — which tool to call next, whether to
query memory, whether to stop — via the ``next`` field on
``strategy.decide.response`` (one of ``"llm" | "tool" | "memory" | "done"``).
The loop controls *order*: strategies cannot change it.

Correlation-via-event-id
------------------------
Request/response event pairs (``strategy.decide.*``, ``llm.call.*``,
``memory.query``/``memory.result``, ``tool.call.request``/``tool.call.result``)
are correlated by **event id**: the loop stamps an outbound request and
awaits a response whose ``payload.request_id`` equals that request's event
id. Plugins responding to a request MUST echo the originating event id
back as ``request_id`` on their response payload. This is how the loop
matches concurrent in-flight calls to the right awaiter without needing
a separate correlation channel.

Context-var escape hatch
------------------------
The bus uses a private ``_IN_WORKER`` :class:`~contextvars.ContextVar` to
make nested ``bus.publish`` calls from inside a handler fire-and-forget
(preventing cross-session / same-session deadlocks — see
``src/yaya/kernel/bus.py``). The loop's per-turn task is created in
response to ``user.message.received``, which itself is delivered *inside*
a session worker; if we naively spawned the turn with
:func:`asyncio.create_task`, ``_IN_WORKER`` would propagate through and
every ``publish`` inside the turn would fire-and-forget, so the loop
would never see its responses resolve (the ``asyncio.Future`` awaited for
a correlation would hang forever — caught only by the per-step timeout).

The loop breaks that chain by spawning the turn task with an **empty**
:class:`contextvars.Context`, which resets every context var — including
``_IN_WORKER`` — back to its default value (``False``). Inside the turn
task, ``publish`` therefore awaits delivery as a top-level caller would,
which is exactly the step-by-step progression the loop needs.

Note: the turn task runs under an EMPTY ``contextvars.Context``. This
resets every ContextVar, not just ``_IN_WORKER``. Adapters or plugins
that rely on ContextVar inheritance for distributed tracing, request
ids, or logging context must propagate those via event payloads (e.g.
an ``x.tracing.span_id`` extension field), not via ContextVars. The
turn task is an intentional context boundary.

Subscription scope
------------------
The loop subscribes to: ``user.message.received``, ``user.interrupt``,
``strategy.decide.response``, ``llm.call.response``, ``llm.call.error``,
``memory.result``, ``tool.call.result``. Each response handler simply
hands the event to :class:`_RequestTracker`, which resolves the matching
future by ``payload.request_id``. Responses whose id is not tracked are
ignored (they may belong to a plugin bypassing the loop, or be late
arrivals after a cancelled turn).

Interrupt delivery latency
--------------------------
``user.interrupt`` is an ordinary public event; it rides the same
per-session FIFO as every other event on its session (see
``docs/dev/plugin-protocol.md``). When a handler is stuck (e.g., a
hung tool plugin), the session worker is inside that handler's
``on_event`` call and cannot deliver the interrupt until the handler
finishes or the bus's per-handler timeout fires (30 s default). UIs
that expect instant Ctrl+C should surface this as "interrupt sent,
waiting up to 30 s" until a fast-interrupt control channel lands
(tracked for 0.2+).

Performance notes
-----------------
* ``memory.write`` is emitted after the turn's ``assistant.message.done``
  and awaited to completion before the turn task returns. A slow memory
  plugin delays the session's next ``user.message.received`` by the
  memory plugin's handler latency. Memory plugins are expected to be
  fast (sqlite INSERT scale). If a plugin is known to be slow, the
  strategy should omit ``write_memory`` and fire writes via a skill
  plugin instead.

Layering
--------
This module depends only on :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
and :mod:`yaya.kernel.plugin`. No imports from ``cli``, ``plugins``, or
``core`` — and no imports of any concrete plugin implementation. The loop
communicates with strategies, LLM providers, tools, and memory exclusively
through the bus.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any

from yaya.kernel.bus import EventBus, Subscription
from yaya.kernel.events import Event, Message, new_event

_logger = logging.getLogger(__name__)

_SOURCE = "kernel"
"""All loop-emitted events carry ``source="kernel"`` — the loop is kernel code."""


@dataclass(slots=True)
class LoopConfig:
    """Tunables for the agent loop.

    Attributes:
        max_iterations: Hard cap on strategy decisions per turn. When the
            loop emits ``max_iterations`` decisions without a
            ``next="done"``, it emits ``kernel.error`` with
            ``message="max_iterations_exceeded"`` and aborts the turn
            without emitting ``assistant.message.done``. The default (16)
            matches the protocol doc's guidance for a ReAct-like strategy.
        step_timeout_s: Per-step deadline the loop waits for a response
            event (strategy decision, LLM response, memory result, tool
            result). Timeouts abort the turn with ``kernel.error``.
            Independent from the bus's per-handler timeout — this one
            guards the request/response round-trip on the wire.
    """

    max_iterations: int = 16
    step_timeout_s: float = 60.0


@dataclass(slots=True)
class _RequestTracker:
    """Map request-event ids to the futures awaiting their response.

    Encapsulates the correlation mechanism so the loop body reads linearly.
    Futures are created by :meth:`track` *before* the request is published,
    then resolved by :meth:`resolve` when a response event carrying the
    matching ``request_id`` arrives.

    Not thread-safe; expected to be used from a single event loop.
    """

    _pending: dict[str, asyncio.Future[Event]] = field(default_factory=dict)

    def track(self, request_id: str) -> asyncio.Future[Event]:
        """Register ``request_id`` and return a fresh future to await on."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Event] = loop.create_future()
        self._pending[request_id] = fut
        return fut

    def resolve(self, ev: Event) -> None:
        """Resolve the awaiter whose request_id matches ``ev.payload``.

        No-op if the event has no ``request_id`` or its id is not tracked —
        the event may belong to another subsystem or be a late response
        for a turn that was already cancelled.
        """
        request_id = ev.payload.get("request_id")
        if not isinstance(request_id, str):
            _logger.warning(
                "response event kind=%r from source=%r carries no 'request_id'; "
                "plugin must echo the originating request.id",
                ev.kind,
                ev.source,
            )
            return
        fut = self._pending.pop(request_id, None)
        if fut is None:
            _logger.debug(
                "untracked response kind=%r request_id=%r (late arrival or cancelled turn)",
                ev.kind,
                request_id,
            )
            return
        if not fut.done():
            fut.set_result(ev)

    def discard(self, request_id: str) -> None:
        """Forget a tracked request without resolving it (e.g. on timeout)."""
        self._pending.pop(request_id, None)

    def cancel_all(self) -> None:
        """Cancel every pending future — called on :meth:`AgentLoop.stop`."""
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.cancel()


@dataclass(slots=True)
class _TurnState:
    """Mutable per-turn bookkeeping threaded through the decision dispatch.

    Kept as a separate object so :meth:`AgentLoop._apply_decision` can mutate
    it without inflating the caller's cyclomatic complexity.
    """

    messages: list[Message]
    last_tool_result: dict[str, Any] | None = None
    last_llm_text: str = ""
    last_tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    """Kernel-owned fixed agent loop.

    One instance drives every session routed through the bus it binds to.
    Per ``user.message.received`` event, the loop spawns a task that
    executes the fixed event sequence above until the strategy says
    ``done`` or a guard trips (max iterations, step timeout, interrupt).

    Thread model: single asyncio event loop. Not safe to share across
    loops; instantiate inside the loop that owns ``bus``.
    """

    def __init__(self, bus: EventBus, config: LoopConfig | None = None) -> None:
        """Bind the loop to a bus.

        Args:
            bus: The running kernel event bus. The loop subscribes only
                after :meth:`start` so tests can wire stubs first.
            config: Tunables; defaults per :class:`LoopConfig`.
        """
        self._bus = bus
        self._config = config or LoopConfig()
        self._tracker = _RequestTracker()
        self._subs: list[Subscription] = []
        self._turns: dict[str, asyncio.Task[None]] = {}
        self._started = False

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to the events that drive the loop.

        Returns once every subscription is registered. Idempotent: a second
        call is a no-op so tests composing multiple layers can call it
        defensively.
        """
        if self._started:
            return
        self._started = True
        self._subs.extend([
            self._bus.subscribe("user.message.received", self._on_user_message, source=_SOURCE),
            self._bus.subscribe("user.interrupt", self._on_interrupt, source=_SOURCE),
            self._bus.subscribe("strategy.decide.response", self._on_response, source=_SOURCE),
            self._bus.subscribe("llm.call.response", self._on_response, source=_SOURCE),
            self._bus.subscribe("llm.call.error", self._on_response, source=_SOURCE),
            self._bus.subscribe("memory.result", self._on_response, source=_SOURCE),
            self._bus.subscribe("tool.call.result", self._on_response, source=_SOURCE),
        ])

    async def stop(self) -> None:
        """Unsubscribe and cancel any in-flight turns; return after drain."""
        if not self._started:
            return
        self._started = False
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()

        turns = list(self._turns.values())
        self._turns.clear()
        for task in turns:
            task.cancel()
        if turns:
            await asyncio.gather(*turns, return_exceptions=True)
        self._tracker.cancel_all()

    # -- bus callbacks ----------------------------------------------------------

    async def _on_user_message(self, ev: Event) -> None:
        """Spawn one turn task per user message.

        The task is created with an **empty** :class:`contextvars.Context`
        so the bus's ``_IN_WORKER`` ContextVar is reset to its default
        (``False``) in the turn. See module docstring for the rationale.
        """
        session_id = ev.session_id
        # If a previous turn is still running on this session, cancel it —
        # the adapter sent a new message, superseding the old turn.
        existing = self._turns.get(session_id)
        if existing is not None and not existing.done():
            existing.cancel()

        ctx = contextvars.Context()
        task = asyncio.get_running_loop().create_task(
            self._run_turn(ev),
            name=f"yaya-turn:{session_id}",
            context=ctx,
        )
        self._turns[session_id] = task

        def _cleanup(done_task: asyncio.Task[None], sid: str = session_id) -> None:
            if self._turns.get(sid) is done_task:
                self._turns.pop(sid, None)

        task.add_done_callback(_cleanup)

    async def _on_interrupt(self, ev: Event) -> None:
        """Cancel the running turn for the interrupting session, if any."""
        task = self._turns.get(ev.session_id)
        if task is None or task.done():
            return
        task.cancel()

    async def _on_response(self, ev: Event) -> None:
        """Route every response kind through the correlation tracker."""
        self._tracker.resolve(ev)

    # -- turn body --------------------------------------------------------------

    async def _run_turn(self, user_ev: Event) -> None:
        """Execute one full turn for ``user_ev``.

        See module docstring for the event sequence. Any guard trip
        (max-iterations, step timeout) surfaces as ``kernel.error`` and
        terminates the turn without emitting ``assistant.message.done``.
        """
        session_id = user_ev.session_id
        state = _TurnState(
            messages=[{"role": "user", "content": user_ev.payload.get("text", "")}],
        )

        try:
            for step in range(self._config.max_iterations):
                decision = await self._decide(session_id, step, state.messages, state.last_tool_result)
                if await self._apply_decision(session_id, decision, state):
                    return

            # Fell off the end without a `done` decision.
            await self._emit_kernel_error(session_id, "max_iterations_exceeded")
        except asyncio.CancelledError:
            # Interrupt or supersession — swallow, do NOT emit assistant.done.
            raise
        except TimeoutError:
            await self._emit_kernel_error(session_id, "step_timeout")
        except Exception as exc:
            _logger.exception("agent loop crashed for session %s", session_id)
            await self._emit_kernel_error(session_id, f"loop_crash: {exc}")

    async def _apply_decision(
        self,
        session_id: str,
        decision: Event,
        state: _TurnState,
    ) -> bool:
        """Apply one strategy decision, returning True when the turn is terminal.

        Returns:
            True if the turn should exit (``done``, ``llm.call.error``, or an
            unknown ``next``); False to continue looping.
        """
        next_step = decision.payload.get("next")
        if next_step == "done":
            await self._publish_assistant_done(session_id, state.last_llm_text, state.last_tool_calls)
            await self._maybe_write_memory(session_id, decision)
            return True
        if next_step == "memory":
            hits = await self._query_memory(session_id, decision)
            state.last_tool_result = {"memory_hits": hits}
            return False
        if next_step == "llm":
            response = await self._call_llm(session_id, decision, state.messages)
            if response.kind == "llm.call.error":
                await self._emit_kernel_error(
                    session_id,
                    f"llm_error: {response.payload.get('error', 'unknown')}",
                )
                return True
            state.last_llm_text = response.payload.get("text", "") or state.last_llm_text
            raw_tool_calls = response.payload.get("tool_calls")
            state.last_tool_calls = list(raw_tool_calls) if isinstance(raw_tool_calls, list) else []
            state.messages.append({"role": "assistant", "content": state.last_llm_text})
            return False
        if next_step == "tool":
            tool_call = decision.payload.get("tool_call") or {}
            result = await self._call_tool(session_id, tool_call)
            state.last_tool_result = dict(result.payload)
            return False
        await self._emit_kernel_error(
            session_id,
            "unknown_strategy_next",
            detail={"next": repr(next_step)},
        )
        return True

    # -- per-step helpers -------------------------------------------------------

    async def _decide(
        self,
        session_id: str,
        step: int,
        messages: list[Message],
        last_tool_result: dict[str, Any] | None,
    ) -> Event:
        """Emit ``strategy.decide.request`` and await the matching response."""
        req = new_event(
            "strategy.decide.request",
            {
                "state": {
                    "session_id": session_id,
                    "step": step,
                    "messages": list(messages),
                    "last_tool_result": last_tool_result,
                },
            },
            session_id=session_id,
            source=_SOURCE,
        )
        return await self._request(req)

    async def _query_memory(self, session_id: str, decision: Event) -> list[dict[str, Any]]:
        """Run a ``memory.query`` round-trip using the strategy's parameters."""
        query = str(decision.payload.get("query", ""))
        k_raw = decision.payload.get("k", 5)
        try:
            k = int(k_raw) if k_raw is not None else 5
        except TypeError, ValueError:
            k = 5
        req = new_event(
            "memory.query",
            {"query": query, "k": k},
            session_id=session_id,
            source=_SOURCE,
        )
        result = await self._request(req)
        hits = result.payload.get("hits") or []
        return list(hits) if isinstance(hits, list) else []

    async def _call_llm(
        self,
        session_id: str,
        decision: Event,
        messages: list[Message],
    ) -> Event:
        """Run an ``llm.call.request`` round-trip.

        The strategy's decision payload supplies provider/model/params; the
        loop assembles the message list it has been accumulating.
        """
        payload: dict[str, Any] = {
            "provider": decision.payload.get("provider") or "",
            "model": decision.payload.get("model") or "",
            "messages": list(messages),
            "params": dict(decision.payload.get("params") or {}),
        }
        tools = decision.payload.get("tools")
        if tools is not None:
            payload["tools"] = tools
        req = new_event("llm.call.request", payload, session_id=session_id, source=_SOURCE)
        return await self._request(req)

    async def _call_tool(self, session_id: str, tool_call: dict[str, Any]) -> Event:
        """Emit ``tool.call.start`` for adapters, then run the tool round-trip."""
        tool_id = str(tool_call.get("id", ""))
        name = str(tool_call.get("name", ""))
        args = dict(tool_call.get("args", {}))

        # Broadcast to adapters so the UI can render progress. This is a
        # fire-and-forget signal; there is no corresponding response event.
        await self._bus.publish(
            new_event(
                "tool.call.start",
                {"id": tool_id, "name": name, "args": args},
                session_id=session_id,
                source=_SOURCE,
            )
        )

        req = new_event(
            "tool.call.request",
            {"id": tool_id, "name": name, "args": args},
            session_id=session_id,
            source=_SOURCE,
        )
        return await self._request(req)

    async def _publish_assistant_done(
        self,
        session_id: str,
        content: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        """Emit the terminal ``assistant.message.done`` event for the turn."""
        await self._bus.publish(
            new_event(
                "assistant.message.done",
                {"content": content, "tool_calls": tool_calls},
                session_id=session_id,
                source=_SOURCE,
            )
        )

    async def _maybe_write_memory(self, session_id: str, decision: Event) -> None:
        """Honor an optional ``memory.write`` request flagged by the strategy."""
        entry = decision.payload.get("write_memory")
        if not isinstance(entry, dict):
            return
        await self._bus.publish(
            new_event(
                "memory.write",
                {"entry": entry},
                session_id=session_id,
                source=_SOURCE,
            )
        )

    # -- request/response plumbing ---------------------------------------------

    async def _request(self, req: Event) -> Event:
        """Publish ``req``, then await the response correlated by event id.

        Raises:
            TimeoutError: If no response arrives within
                :attr:`LoopConfig.step_timeout_s`.
            asyncio.CancelledError: Propagated when the turn is cancelled
                (interrupt or supersession).
        """
        fut = self._tracker.track(req.id)
        try:
            await self._bus.publish(req)
            return await asyncio.wait_for(fut, timeout=self._config.step_timeout_s)
        except BaseException:
            self._tracker.discard(req.id)
            raise

    async def _emit_kernel_error(
        self,
        session_id: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Publish ``kernel.error`` tagged with ``source="agent_loop"``.

        Args:
            session_id: Session whose turn produced the error.
            message: Short machine-readable error tag (e.g. ``"step_timeout"``).
            detail: Optional structured context (e.g. the offending strategy
                ``next`` value, raw tool args) for adapters to parse.
        """
        payload: dict[str, Any] = {"source": "agent_loop", "message": message}
        if detail is not None:
            payload["detail"] = detail
        await self._bus.publish(
            new_event(
                "kernel.error",
                payload,
                session_id=session_id,
                source=_SOURCE,
            )
        )


__all__ = ["AgentLoop", "LoopConfig"]
