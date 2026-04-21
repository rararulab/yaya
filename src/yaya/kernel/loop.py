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
from typing import Any, cast

from yaya.kernel.bus import EventBus, Subscription
from yaya.kernel.events import Event, Message, new_event
from yaya.kernel.payload import (
    payload_dict,
    payload_int,
    payload_list_of_dicts,
    payload_str,
)

_logger = logging.getLogger(__name__)

_SOURCE = "kernel"
"""All loop-emitted events carry ``source="kernel"`` — the loop is kernel code."""


def _format_tool_result_for_llm(result_payload: dict[str, Any]) -> str:
    """Serialise a ``tool.call.result`` payload for a ReAct Observation.

    The caller wraps the returned string as
    ``role="user" content="Observation: <this>"`` before appending it
    to the conversation. We prefer compact structured JSON so the
    model can parse fields (stdout / stderr / error) reliably, but
    fall back to the stdout-only shape when the tool returned a flat
    ``value`` that already reads well as text.
    """
    import json

    if result_payload.get("ok") is False:
        return json.dumps({"ok": False, "error": result_payload.get("error", "unknown")})
    value: Any = result_payload.get("value")
    if isinstance(value, dict):
        typed_value = cast("dict[str, Any]", value)
        if {"stdout", "stderr", "returncode"} <= set(typed_value.keys()):
            return json.dumps(typed_value)
    if isinstance(value, str):
        return value
    return json.dumps({"ok": True, "value": value})


def _project_entries_to_messages(entries: list[Any]) -> list[Message]:
    """Project raw tape entries onto a cross-turn ``messages`` list.

    Only ``kind="message"`` entries contribute; tool calls / results /
    observational events are skipped because the loop's ReAct format
    folds tool outputs into ``role="user"`` ``Observation:`` messages
    which the persister already records as ``message`` entries. When a
    compaction anchor (``kind="anchor"`` with
    ``payload.state.kind == "compaction"``) is encountered, every
    previously-accumulated message is discarded and the anchor's
    ``summary`` is injected as a ``role="system"`` message — same
    contract as :func:`yaya.kernel.tape_context.select_messages` so
    turn history and context-selector output stay consistent.

    Kept module-level so unit tests can exercise it without spinning
    up a full :class:`AgentLoop`.
    """
    messages: list[Message] = []
    for entry in entries:
        kind = cast("Any", getattr(entry, "kind", None))
        raw_payload: Any = getattr(entry, "payload", None)
        payload: dict[str, Any] = dict(cast("dict[str, Any]", raw_payload)) if isinstance(raw_payload, dict) else {}
        if kind == "anchor":
            state_value: Any = payload.get("state")
            if isinstance(state_value, dict):
                state: dict[str, Any] = cast("dict[str, Any]", state_value)
                if state.get("kind") == "compaction":
                    summary_raw: Any = state.get("summary", "")
                    summary = summary_raw if isinstance(summary_raw, str) else str(summary_raw)
                    messages = []
                    messages.append({
                        "role": "system",
                        "content": f"[compacted history]\n{summary}" if summary else "[compacted history]",
                    })
            continue
        if kind != "message":
            continue
        role: Any = payload.get("role")
        content: Any = payload.get("content", "")
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        messages.append({"role": role, "content": content})
    return messages


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

    _pending: dict[str, asyncio.Future[Event]] = field(default_factory=lambda: {})

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
    last_tool_calls: list[dict[str, Any]] = field(default_factory=lambda: [])


class AgentLoop:
    """Kernel-owned fixed agent loop.

    One instance drives every session routed through the bus it binds to.
    Per ``user.message.received`` event, the loop spawns a task that
    executes the fixed event sequence above until the strategy says
    ``done`` or a guard trips (max iterations, step timeout, interrupt).

    Thread model: single asyncio event loop. Not safe to share across
    loops; instantiate inside the loop that owns ``bus``.
    """

    def __init__(
        self,
        bus: EventBus,
        config: LoopConfig | None = None,
        *,
        session_store: Any = None,
        workspace: Any = None,
    ) -> None:
        """Bind the loop to a bus.

        Args:
            bus: The running kernel event bus. The loop subscribes only
                after :meth:`start` so tests can wire stubs first.
            config: Tunables; defaults per :class:`LoopConfig`.
            session_store: Optional :class:`~yaya.kernel.session.SessionStore`
                used to hydrate cross-turn conversation history at the
                start of each turn. Typed as :data:`Any` to keep the
                kernel import graph acyclic (mirrors the pattern in
                :class:`~yaya.kernel.session_persister.SessionPersister`).
                When ``None`` — the default, used by ``yaya hello`` and
                the loop unit tests — each turn starts from a fresh
                single-message state, preserving 0.1 semantics.
            workspace: Workspace path passed to
                :meth:`SessionStore.open` when hydrating history. Only
                consulted when ``session_store`` is not ``None``.
        """
        self._bus = bus
        self._config = config or LoopConfig()
        self._tracker = _RequestTracker()
        self._subs: list[Subscription] = []
        self._turns: dict[str, asyncio.Task[None]] = {}
        self._started = False
        self._session_store = session_store
        self._workspace = workspace

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

    async def _load_history(self, session_id: str, user_text: str) -> list[Message]:
        """Hydrate cross-turn history for ``session_id`` ahead of the turn.

        Behaviour:

        * When no ``session_store`` was wired, return the 0.1 single-
          message state so tests and ``yaya hello`` keep working.
        * Otherwise open the session, read every ``kind="message"`` tape
          entry, and project it to ``{role, content}``. If the tape
          contains one or more compaction anchors (written by
          :func:`yaya.kernel.compaction.compact_session`), only entries
          **after the most recent** compaction anchor are kept and the
          anchor's ``summary`` is injected as a leading ``role="system"``
          message — this mirrors the selection in
          :func:`yaya.kernel.tape_context.select_messages` and keeps the
          cross-turn view in agreement with compaction's contract.
        * The persister mirrors the incoming ``user.message.received``
          event onto the tape on the same session worker, so the current
          user message may already appear as the trailing entry when the
          turn task reads the tape. Drop that duplicate and append the
          fresh message from ``user_ev`` unconditionally so the outgoing
          history always ends with the exact text the turn is handling.

        Failures fall back to the 0.1 behaviour (log + single-message
        state) — a hydration crash must not take the session down.
        """
        fallback: list[Message] = [{"role": "user", "content": user_text}]
        store = self._session_store
        if store is None or self._workspace is None:
            return fallback
        try:
            session = await store.open(self._workspace, session_id)
            entries = await session.entries()
        except Exception:
            _logger.exception(
                "agent loop failed to hydrate history for session %r; falling back to single-message turn state",
                session_id,
            )
            return fallback

        messages = _project_entries_to_messages(entries)
        # The persister's write may have landed before our read; avoid
        # surfacing the current user message twice. A trailing match is
        # the only possible duplicate (tape is append-only).
        if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == user_text:
            messages.pop()
        messages.append({"role": "user", "content": user_text})
        return messages

    async def _run_turn(self, user_ev: Event) -> None:
        """Execute one full turn for ``user_ev``.

        See module docstring for the event sequence. Any guard trip
        (max-iterations, step timeout) surfaces as ``kernel.error`` and
        terminates the turn without emitting ``assistant.message.done``.
        """
        session_id = user_ev.session_id
        user_text = payload_str(user_ev.payload, "text")
        initial_messages = await self._load_history(session_id, user_text)
        state = _TurnState(messages=initial_messages)

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
            # A strategy may append corrective or priming messages to
            # ``state.messages`` before the LLM call (e.g. ReAct's
            # format-nudge on a parse failure). Apply those first so
            # the outgoing history reflects them.
            for m in payload_list_of_dicts(decision.payload, "messages_append"):
                state.messages.append(cast("Message", m))
            # ``messages_prepend`` carries transient priming context
            # (e.g. ReAct's system prompt) that the strategy does NOT
            # want persisted in ``state.messages``. It rides only on
            # this single request.
            prepend = payload_list_of_dicts(decision.payload, "messages_prepend")
            outgoing: list[Message] = [cast("Message", m) for m in prepend] + list(state.messages)
            response = await self._call_llm(session_id, decision, outgoing)
            if response.kind == "llm.call.error":
                await self._emit_kernel_error(
                    session_id,
                    f"llm_error: {payload_str(response.payload, 'error', 'unknown')}",
                )
                return True
            state.last_llm_text = payload_str(response.payload, "text") or state.last_llm_text
            state.last_tool_calls = payload_list_of_dicts(response.payload, "tool_calls")
            # Clear any pending tool/memory result now that the LLM
            # has had a chance to consume it (#147).
            state.last_tool_result = None
            # ReAct strategy drives tool intent through free-form
            # ``Thought: / Action: / Action Input:`` text inside the
            # assistant ``content``, not through OpenAI's structured
            # ``tool_calls`` field. Persisting ``tool_calls`` here
            # would confuse a text-only replay, so the appended
            # assistant message carries ``content`` only.
            assistant_msg: Message = {
                "role": "assistant",
                "content": state.last_llm_text,
            }
            state.messages.append(assistant_msg)
            return False
        if next_step == "tool":
            tool_call = payload_dict(decision.payload, "tool_call")
            result = await self._call_tool(session_id, tool_call)
            state.last_tool_result = dict(result.payload)
            # ReAct protocol: tool results come back as a ``role="user"``
            # message whose content begins with ``Observation:`` — the
            # canonical ReAct shape the system prompt tells the model
            # to expect. No ``role="tool"`` / ``tool_call_id`` — those
            # belong to OpenAI function calling, which we no longer
            # use here.
            observation = _format_tool_result_for_llm(result.payload)
            tool_msg: Message = {
                "role": "user",
                "content": f"Observation: {observation}",
            }
            state.messages.append(tool_msg)
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
        query = payload_str(decision.payload, "query")
        k = payload_int(decision.payload, "k", 5)
        req = new_event(
            "memory.query",
            {"query": query, "k": k},
            session_id=session_id,
            source=_SOURCE,
        )
        result = await self._request(req)
        return payload_list_of_dicts(result.payload, "hits")

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
        llm_payload: dict[str, Any] = {
            "provider": payload_str(decision.payload, "provider"),
            "model": payload_str(decision.payload, "model"),
            "messages": list(messages),
            "params": payload_dict(decision.payload, "params"),
        }
        tools = payload_list_of_dicts(decision.payload, "tools")
        if tools:
            llm_payload["tools"] = tools
        req = new_event("llm.call.request", llm_payload, session_id=session_id, source=_SOURCE)
        return await self._request(req)

    async def _call_tool(self, session_id: str, tool_call: dict[str, Any]) -> Event:
        """Emit ``tool.call.start`` for adapters, then run the tool round-trip."""
        tool_id = payload_str(tool_call, "id")
        name = payload_str(tool_call, "name")
        args = payload_dict(tool_call, "args")

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
        entry = payload_dict(decision.payload, "write_memory")
        if not entry:
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
