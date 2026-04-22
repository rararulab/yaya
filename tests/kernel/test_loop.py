"""Tests for :class:`yaya.kernel.loop.AgentLoop`.

Every test wires stub plugins directly to the bus (no registry yet —
issue #13) and publishes a ``user.message.received`` event to drive one
turn. Stubs echo ``request_id`` back on their response payloads so the
loop's correlation mechanism resolves each step.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.loop import AgentLoop, LoopConfig

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub plugins — each subscribes directly to the bus (no registry at #12).
# ---------------------------------------------------------------------------


@dataclass
class FakeStrategy:
    """Pre-scripted strategy plugin.

    Returns decisions from ``script`` in order. Each decision is merged
    with ``{"next": ...}`` plus any extra payload fields (e.g. the LLM
    provider/model for a ``next=llm`` step).
    """

    bus: EventBus
    script: list[dict[str, Any]]
    calls: int = 0
    _sub: Any = None

    def subscribe(self) -> None:
        self._sub = self.bus.subscribe("strategy.decide.request", self._on_request, source="fake-strategy")

    async def _on_request(self, ev: Event) -> None:
        idx = min(self.calls, len(self.script) - 1)
        decision = dict(self.script[idx])
        self.calls += 1
        decision["request_id"] = ev.id
        await self.bus.publish(
            new_event(
                "strategy.decide.response",
                decision,
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )


@dataclass
class FakeLLM:
    """Stub LLM provider returning a canned response."""

    bus: EventBus
    text: str = "hello"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0

    def subscribe(self) -> None:
        self.bus.subscribe("llm.call.request", self._on_request, source="fake-llm")

    async def _on_request(self, ev: Event) -> None:
        self.calls += 1
        await self.bus.publish(
            new_event(
                "llm.call.response",
                {
                    "text": self.text,
                    "tool_calls": list(self.tool_calls),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "request_id": ev.id,
                },
                session_id=ev.session_id,
                source="fake-llm",
            )
        )


@dataclass
class FakeTool:
    """Stub tool that echoes its args back as the value."""

    bus: EventBus
    calls: list[dict[str, Any]] = field(default_factory=list)

    def subscribe(self) -> None:
        self.bus.subscribe("tool.call.request", self._on_request, source="fake-tool")

    async def _on_request(self, ev: Event) -> None:
        self.calls.append(dict(ev.payload))
        await self.bus.publish(
            new_event(
                "tool.call.result",
                {
                    "id": ev.payload["id"],
                    "ok": True,
                    "value": ev.payload.get("args", {}),
                    "request_id": ev.id,
                },
                session_id=ev.session_id,
                source="fake-tool",
            )
        )


@dataclass
class FakeMemory:
    """Stub memory plugin returning a configured hit list."""

    bus: EventBus
    hits: list[dict[str, Any]] = field(default_factory=list)

    def subscribe(self) -> None:
        self.bus.subscribe("memory.query", self._on_query, source="fake-memory")

    async def _on_query(self, ev: Event) -> None:
        await self.bus.publish(
            new_event(
                "memory.result",
                {"hits": list(self.hits), "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-memory",
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class EventRecorder:
    """Records every event of the given kinds seen on the bus."""

    bus: EventBus
    events: list[Event] = field(default_factory=list)

    def watch(self, kind: str) -> None:
        self.bus.subscribe(kind, self._record, source="recorder")

    async def _record(self, ev: Event) -> None:
        self.events.append(ev)


async def _settle(bus: EventBus) -> None:
    """Yield enough times for all session workers to drain."""
    for _ in range(20):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# AC-01: happy path
# ---------------------------------------------------------------------------


async def test_happy_path() -> None:
    """@AC-01 — user message → strategy(llm→done) → assistant.message.done."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "llm", "provider": "fake", "model": "m"},
            {"next": "done"},
        ],
    )
    llm = FakeLLM(bus, text="hello")
    strategy.subscribe()
    llm.subscribe()

    recorder = EventRecorder(bus)
    recorder.watch("assistant.message.done")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "hi"},
            session_id="s-1",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(recorder.events) == 1
    done = recorder.events[0]
    assert done.payload["content"] == "hello"
    assert strategy.calls == 2
    assert llm.calls == 1


# ---------------------------------------------------------------------------
# AC-02: tool round-trip
# ---------------------------------------------------------------------------


async def test_tool_roundtrip() -> None:
    """@AC-02 — a `next=tool` step produces tool.call.start + tool.call.result before done."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {
                "next": "tool",
                "tool_call": {"id": "t1", "name": "echo", "args": {"x": 1}},
            },
            {"next": "llm", "provider": "fake", "model": "m"},
            {"next": "done"},
        ],
    )
    llm = FakeLLM(bus, text="post-tool")
    tool = FakeTool(bus)
    strategy.subscribe()
    llm.subscribe()
    tool.subscribe()

    order: list[str] = []

    async def track_start(_: Event) -> None:
        order.append("tool.call.start")

    async def track_result(_: Event) -> None:
        order.append("tool.call.result")

    async def track_done(_: Event) -> None:
        order.append("assistant.message.done")

    bus.subscribe("tool.call.start", track_start, source="probe")
    bus.subscribe("tool.call.result", track_result, source="probe")
    bus.subscribe("assistant.message.done", track_done, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "run it"},
            session_id="s-2",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert "tool.call.start" in order
    assert "tool.call.result" in order
    assert "assistant.message.done" in order
    # Ordering: tool.call.start and tool.call.result precede assistant.message.done.
    assert order.index("tool.call.start") < order.index("assistant.message.done")
    assert order.index("tool.call.result") < order.index("assistant.message.done")
    assert tool.calls == [
        {
            "id": "t1",
            "name": "echo",
            "args": {"x": 1},
        }
    ]


# ---------------------------------------------------------------------------
# AC-03: max-iterations guard
# ---------------------------------------------------------------------------


async def test_max_iterations_guard() -> None:
    """@AC-03 — a strategy that never says 'done' trips max_iterations."""
    bus = EventBus()
    # A memory step is the cheapest — no LLM or tool plugin required.
    strategy = FakeStrategy(bus, script=[{"next": "memory", "query": "q", "k": 1}])
    memory = FakeMemory(bus, hits=[])
    strategy.subscribe()
    memory.subscribe()

    errors: list[Event] = []
    dones: list[Event] = []

    async def on_error(ev: Event) -> None:
        errors.append(ev)

    async def on_done(ev: Event) -> None:
        dones.append(ev)

    bus.subscribe("kernel.error", on_error, source="probe")
    bus.subscribe("assistant.message.done", on_done, source="probe")

    loop = AgentLoop(bus, LoopConfig(max_iterations=3, step_timeout_s=2.0))
    await loop.start()

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "loop me"},
            session_id="s-3",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(errors) == 1
    assert errors[0].payload == {
        "source": "agent_loop",
        "message": "max_iterations_exceeded",
    }
    assert dones == []
    assert strategy.calls == 3


# ---------------------------------------------------------------------------
# AC-04: interrupt aborts the turn
# ---------------------------------------------------------------------------


async def test_interrupt_aborts_turn() -> None:
    """@AC-04 — user.interrupt cancels the running turn; no further tool calls."""
    bus = EventBus()

    tool_requests: list[Event] = []

    # A strategy that schedules a tool call and nothing else. The tool
    # handler returns immediately without emitting tool.call.result so the
    # loop is stuck awaiting correlation — the perfect mid-turn moment.
    strategy = FakeStrategy(
        bus,
        script=[
            {
                "next": "tool",
                "tool_call": {"id": "t-slow", "name": "slow", "args": {}},
            }
        ],
    )
    strategy.subscribe()

    async def never_responding_tool(ev: Event) -> None:
        # Record the request but do NOT publish tool.call.result. The loop's
        # correlation future will remain pending until interrupt cancels it.
        tool_requests.append(ev)

    bus.subscribe("tool.call.request", never_responding_tool, source="slow-tool")

    dones: list[Event] = []

    async def on_done(ev: Event) -> None:
        dones.append(ev)

    bus.subscribe("assistant.message.done", on_done, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=5.0))
    await loop.start()

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "start"},
            session_id="s-4",
            source="fake-adapter",
        )
    )
    # Let the loop reach the tool.call.request stage.
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(tool_requests) == 1

    before = len(tool_requests)

    # Interrupt.
    await bus.publish(
        new_event(
            "user.interrupt",
            {},
            session_id="s-4",
            source="fake-adapter",
        )
    )
    # Allow cancellation to propagate.
    for _ in range(20):
        await asyncio.sleep(0)

    await loop.stop()
    await bus.close()

    # No assistant.message.done, and no *additional* tool.call.request emitted
    # after the interrupt.
    assert dones == []
    assert len(tool_requests) == before
    # Strategy was asked exactly once — the second decision never happened
    # because the turn was cancelled while awaiting the tool result.
    assert strategy.calls == 1


# ---------------------------------------------------------------------------
# Extra — lifecycle edge cases for coverage.
# ---------------------------------------------------------------------------


async def test_start_is_idempotent() -> None:
    """Calling :meth:`AgentLoop.start` twice must not double-subscribe."""
    bus = EventBus()
    loop = AgentLoop(bus)
    await loop.start()
    await loop.start()
    await loop.stop()
    await loop.stop()  # also idempotent
    await bus.close()


async def test_loop_config_defaults() -> None:
    """Defaults from the issue spec: 16 iterations, 60s step timeout."""
    cfg = LoopConfig()
    assert cfg.max_iterations == 16
    assert cfg.step_timeout_s == pytest.approx(60.0)


async def test_memory_then_done_emits_write_when_requested() -> None:
    """A decision with ``write_memory`` should trigger a memory.write on done."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "memory", "query": "hello", "k": 2},
            {"next": "llm", "provider": "fake", "model": "m"},
            {
                "next": "done",
                "write_memory": {"id": "m1", "text": "remember", "meta": {}},
            },
        ],
    )
    memory = FakeMemory(bus, hits=[{"id": "h1", "text": "hit"}])
    llm = FakeLLM(bus, text="ok")
    strategy.subscribe()
    memory.subscribe()
    llm.subscribe()

    writes: list[Event] = []

    async def on_write(ev: Event) -> None:
        writes.append(ev)

    bus.subscribe("memory.write", on_write, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "hi"},
            session_id="s-mem",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(writes) == 1
    assert writes[0].payload["entry"] == {"id": "m1", "text": "remember", "meta": {}}


async def test_unknown_strategy_next_emits_kernel_error() -> None:
    """A decision with a ``next`` value outside the closed set aborts the turn."""
    bus = EventBus()
    strategy = FakeStrategy(bus, script=[{"next": "bogus"}])
    strategy.subscribe()

    errors: list[Event] = []

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("kernel.error", on_err, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(new_event("user.message.received", {"text": "hi"}, session_id="s-b", source="a"))
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(errors) == 1
    assert "unknown_strategy_next" in errors[0].payload["message"]


async def test_step_timeout_emits_kernel_error() -> None:
    """When no response arrives within step_timeout_s, kernel.error surfaces."""
    bus = EventBus()
    # Strategy subscribes but never responds.

    async def silent(_: Event) -> None:
        return None

    bus.subscribe("strategy.decide.request", silent, source="silent-strategy")

    errors: list[Event] = []

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("kernel.error", on_err, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=0.05))
    await loop.start()
    await bus.publish(new_event("user.message.received", {"text": "go"}, session_id="s-t", source="a"))
    # Wait long enough for the step timeout to trip.
    await asyncio.sleep(0.1)
    for _ in range(10):
        await asyncio.sleep(0)
    await loop.stop()
    await bus.close()

    assert any(e.payload["message"] == "step_timeout" for e in errors)


async def test_interrupt_with_no_active_turn_is_noop() -> None:
    """user.interrupt on a session with no running turn is silently ignored."""
    bus = EventBus()
    loop = AgentLoop(bus)
    await loop.start()
    # No user.message.received — no turn exists for this session.
    await bus.publish(new_event("user.interrupt", {}, session_id="s-idle", source="a"))
    await _settle(bus)
    await loop.stop()
    await bus.close()


async def test_untracked_response_is_ignored() -> None:
    """A response with a request_id the tracker does not know is dropped quietly."""
    bus = EventBus()
    loop = AgentLoop(bus)
    await loop.start()
    # This llm.call.response has no matching outbound request — must not crash.
    await bus.publish(
        new_event(
            "llm.call.response",
            {"usage": {}, "text": "stray", "request_id": "unknown-id"},
            session_id="s-stray",
            source="rogue-llm",
        )
    )
    # And one without request_id at all.
    await bus.publish(
        new_event(
            "memory.result",
            {"hits": []},
            session_id="s-stray",
            source="rogue-memory",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()


async def test_new_message_supersedes_running_turn() -> None:
    """A second user.message.received on the same session cancels the first turn."""
    bus = EventBus()

    async def silent(_: Event) -> None:
        return None

    # Strategy never responds, so the first turn hangs until superseded.
    bus.subscribe("strategy.decide.request", silent, source="silent")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=5.0))
    await loop.start()

    await bus.publish(new_event("user.message.received", {"text": "one"}, session_id="s-sup", source="a"))
    for _ in range(5):
        await asyncio.sleep(0)
    # Now send a second message — this must cancel the first turn task.
    await bus.publish(new_event("user.message.received", {"text": "two"}, session_id="s-sup", source="a"))
    for _ in range(5):
        await asyncio.sleep(0)
    await loop.stop()
    await bus.close()


async def test_llm_error_surfaces_kernel_error() -> None:
    """An ``llm.call.error`` in response to a call must abort with kernel.error."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[{"next": "llm", "provider": "fake", "model": "m"}, {"next": "done"}],
    )
    strategy.subscribe()

    async def on_llm_request(ev: Event) -> None:
        await bus.publish(
            new_event(
                "llm.call.error",
                {"error": "rate_limited", "request_id": ev.id},
                session_id=ev.session_id,
                source="failing-llm",
            )
        )

    bus.subscribe("llm.call.request", on_llm_request, source="failing-llm")

    errors: list[Event] = []

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("kernel.error", on_err, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "go"},
            session_id="s-err",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(errors) == 1
    assert errors[0].payload["source"] == "agent_loop"
    assert "llm_error" in errors[0].payload["message"]


# ---------------------------------------------------------------------------
# P2 #3 regression: missing request_id on a response logs a WARNING.
# ---------------------------------------------------------------------------


async def test_response_without_request_id_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A response event with no ``request_id`` logs a WARNING, not silence."""
    bus = EventBus()
    loop = AgentLoop(bus)
    await loop.start()
    with caplog.at_level(logging.WARNING, logger="yaya.kernel.loop"):
        await bus.publish(
            new_event(
                "memory.result",
                {"hits": []},
                session_id="s-warn",
                source="rogue-memory",
            )
        )
        await _settle(bus)
    await loop.stop()
    await bus.close()

    assert any("request_id" in rec.getMessage() and rec.levelno == logging.WARNING for rec in caplog.records)


# ---------------------------------------------------------------------------
# P2 #5 regression: non-numeric ``k`` in memory.query defaults to 5.
# ---------------------------------------------------------------------------


async def test_memory_query_with_nonnumeric_k_defaults_to_5() -> None:
    """A strategy passing a non-numeric ``k`` must not crash the loop."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "memory", "query": "q", "k": "not-a-number"},
            {"next": "llm", "provider": "fake", "model": "m"},
            {"next": "done"},
        ],
    )
    memory = FakeMemory(bus, hits=[])
    llm = FakeLLM(bus, text="ok")
    strategy.subscribe()
    memory.subscribe()
    llm.subscribe()

    memory_queries: list[Event] = []

    async def watch_query(ev: Event) -> None:
        memory_queries.append(ev)

    bus.subscribe("memory.query", watch_query, source="probe")

    dones: list[Event] = []

    async def on_done(ev: Event) -> None:
        dones.append(ev)

    bus.subscribe("assistant.message.done", on_done, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "hi"},
            session_id="s-k",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(memory_queries) == 1
    assert memory_queries[0].payload["k"] == 5
    assert len(dones) == 1


# ---------------------------------------------------------------------------
# P2 #7 regression: assistant.message.done carries the LLM's last tool_calls.
# ---------------------------------------------------------------------------


async def test_turn_with_no_tape_behaves_like_before() -> None:
    """#156 regression: without a session_store the turn state is one user message.

    Guards the 0.1 fallback — ``yaya hello`` and bundled unit tests
    construct ``AgentLoop(bus)`` with no store, and must keep starting
    turns with exactly the incoming user message (no crash, no extra
    history pulled from thin air).
    """
    bus = EventBus()
    observed: list[list[dict[str, Any]]] = []

    async def record_decide(ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        observed.append(list(msgs))
        await bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )

    bus.subscribe("strategy.decide.request", record_decide, source="fake-strategy")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "solo"},
            session_id="s-nohist",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert observed == [[{"role": "user", "content": "solo"}]]


@dataclass
class _FakeSession:
    """Stub :class:`~yaya.kernel.session.Session` returning pre-scripted entries.

    The real Session reads from a :class:`~republic.TapeEntry` store; we
    just need ``entries()`` to return objects with ``kind`` / ``payload``
    so ``_project_entries_to_messages`` sees tape-shaped data. Using a
    lightweight stand-in keeps the test free of filesystem / republic
    wiring — the hydration contract is stable on the object shape.
    """

    scripted: list[Any]
    provider_calls: list[tuple[str, str]] = field(default_factory=lambda: [])

    async def entries(self) -> list[Any]:
        return list(self.scripted)

    async def append_turn_provider(self, provider: str, model: str) -> None:
        # Record the call so #163 loop tests can assert the anchor was
        # stamped exactly once per turn with the expected pair.
        self.provider_calls.append((provider, model))


@dataclass
class _FakeEntry:
    kind: str
    payload: dict[str, Any]


@dataclass
class _FakeStore:
    """Minimal ``SessionStore`` duck: ``open(workspace, session_id) -> Session``."""

    session: _FakeSession

    async def open(self, _workspace: Any, _session_id: str) -> _FakeSession:
        return self.session


async def test_turn_loads_prior_messages_from_tape() -> None:
    """#156 AC-01: prior user + assistant messages ride into the turn's state.

    A session with one completed exchange on the tape should yield a
    three-message state on the next turn: prior user → prior assistant
    → new user message. The persister race path is not exercised here
    (tape is scripted before the turn runs).
    """
    tape: list[_FakeEntry] = [
        _FakeEntry("anchor", {"name": "session/start", "state": {"owner": "human"}}),
        _FakeEntry("message", {"role": "user", "content": "hi"}),
        _FakeEntry("message", {"role": "assistant", "content": "hello"}),
    ]
    store = _FakeStore(_FakeSession(tape))

    bus = EventBus()
    observed: list[list[dict[str, Any]]] = []

    async def record_decide(ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        observed.append(list(msgs))
        await bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )

    bus.subscribe("strategy.decide.request", record_decide, source="fake-strategy")

    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=store,
        workspace="/fake/ws",
    )
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "what did I say?"},
            session_id="s-hist",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert observed == [
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "what did I say?"},
        ]
    ]


async def test_turn_skips_history_before_latest_compaction_anchor() -> None:
    """#156 AC-02: compaction anchor elides the pre-anchor prefix.

    Tape shape: msg-A, msg-B, compaction-anchor, msg-C. The hydrated
    turn state must drop msg-A and msg-B, inject the compaction summary
    as a ``role="system"`` message, keep msg-C, and append the new
    user message. Mirrors the contract in
    :func:`yaya.kernel.tape_context.select_messages`.
    """
    tape: list[_FakeEntry] = [
        _FakeEntry("anchor", {"name": "session/start", "state": {"owner": "human"}}),
        _FakeEntry("message", {"role": "user", "content": "msg-A"}),
        _FakeEntry("message", {"role": "assistant", "content": "msg-B"}),
        _FakeEntry(
            "anchor",
            {
                "name": "compaction",
                "state": {"kind": "compaction", "summary": "prior chat summarised"},
            },
        ),
        _FakeEntry("message", {"role": "user", "content": "msg-C"}),
    ]
    store = _FakeStore(_FakeSession(tape))

    bus = EventBus()
    observed: list[list[dict[str, Any]]] = []

    async def record_decide(ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        observed.append(list(msgs))
        await bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )

    bus.subscribe("strategy.decide.request", record_decide, source="fake-strategy")

    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=store,
        workspace="/fake/ws",
    )
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "follow up"},
            session_id="s-compact",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert observed == [
        [
            {"role": "system", "content": "[compacted history]\nprior chat summarised"},
            {"role": "user", "content": "msg-C"},
            {"role": "user", "content": "follow up"},
        ]
    ]


async def test_turn_deduplicates_trailing_current_user_message() -> None:
    """#156 edge case: the persister may race ahead and add the current msg.

    If the tape already contains the incoming user message as its
    trailing entry (because the persister ran before the turn task read
    the tape), the loop must not surface it twice. The fresh message
    from ``user_ev.payload`` is the single source of truth for the new
    turn; historical duplicates higher up the tape are legitimate and
    preserved.
    """
    tape: list[_FakeEntry] = [
        _FakeEntry("message", {"role": "user", "content": "follow up"}),  # persister race
    ]
    store = _FakeStore(_FakeSession(tape))

    bus = EventBus()
    observed: list[list[dict[str, Any]]] = []

    async def record_decide(ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        observed.append(list(msgs))
        await bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )

    bus.subscribe("strategy.decide.request", record_decide, source="fake-strategy")

    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=store,
        workspace="/fake/ws",
    )
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "follow up"},
            session_id="s-dedup",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert observed == [[{"role": "user", "content": "follow up"}]]


async def test_turn_hydration_failure_falls_back_to_single_message() -> None:
    """#156: a raising store must not poison the session worker."""

    class _BoomStore:
        async def open(self, _workspace: Any, _session_id: str) -> Any:
            raise RuntimeError("tape unavailable")

    bus = EventBus()
    observed: list[list[dict[str, Any]]] = []

    async def record_decide(ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        observed.append(list(msgs))
        await bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="fake-strategy",
            )
        )

    bus.subscribe("strategy.decide.request", record_decide, source="fake-strategy")

    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=_BoomStore(),
        workspace="/fake/ws",
    )
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "still works"},
            session_id="s-boom",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert observed == [[{"role": "user", "content": "still works"}]]


async def test_assistant_done_carries_last_llm_tool_calls() -> None:
    """When the LLM response includes tool_calls, they propagate to done."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "llm", "provider": "fake", "model": "m"},
            {"next": "done"},
        ],
    )
    expected_calls = [{"id": "c1", "name": "search", "args": {"q": "hi"}}]
    llm = FakeLLM(bus, text="answer", tool_calls=expected_calls)
    strategy.subscribe()
    llm.subscribe()

    dones: list[Event] = []

    async def on_done(ev: Event) -> None:
        dones.append(ev)

    bus.subscribe("assistant.message.done", on_done, source="probe")

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "hi"},
            session_id="s-tc",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert len(dones) == 1
    assert dones[0].payload["tool_calls"] == expected_calls


# ---------------------------------------------------------------------------
# #163 — turn/provider anchor stamped once per turn
# ---------------------------------------------------------------------------


async def test_turn_writes_provider_anchor() -> None:
    """#163 AC-01: the loop stamps a turn/provider anchor once per turn.

    Scripts a two-call ReAct turn (llm → tool → llm → done) and asserts
    the fake session only receives ONE ``append_turn_provider`` call —
    the second llm step must not re-stamp. The recorded pair is the
    provider/model the strategy emitted.
    """
    tape: list[_FakeEntry] = [
        _FakeEntry("anchor", {"name": "session/start", "state": {"owner": "human"}}),
    ]
    fake_session = _FakeSession(scripted=tape)
    store = _FakeStore(fake_session)

    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "llm", "provider": "llm-openai-prod", "model": "gpt-4o-mini"},
            {"next": "tool", "tool_call": {"id": "t1", "name": "echo", "args": {}}},
            {"next": "llm", "provider": "llm-openai-prod", "model": "gpt-4o-mini"},
            {"next": "done"},
        ],
    )
    llm = FakeLLM(bus, text="ok")
    tool = FakeTool(bus)
    strategy.subscribe()
    llm.subscribe()
    tool.subscribe()

    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=store,
        workspace="/fake/ws",
    )
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "go"},
            session_id="s-anchor",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()

    assert fake_session.provider_calls == [("llm-openai-prod", "gpt-4o-mini")], (
        f"expected exactly one anchor per turn; got {fake_session.provider_calls!r}"
    )


async def test_turn_skips_provider_anchor_without_session_store() -> None:
    """Without a wired session_store the loop never attempts the anchor write (#163)."""
    bus = EventBus()
    strategy = FakeStrategy(
        bus,
        script=[
            {"next": "llm", "provider": "llm-openai", "model": "m"},
            {"next": "done"},
        ],
    )
    llm = FakeLLM(bus, text="ok")
    strategy.subscribe()
    llm.subscribe()

    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "go"},
            session_id="s-nostore",
            source="fake-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()
    # Completing with no store wired should not raise — the test
    # passing is the assertion.
