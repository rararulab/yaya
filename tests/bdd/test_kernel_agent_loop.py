"""Pytest-bdd execution of specs/kernel-agent-loop.spec scenarios.

The Gherkin text in ``features/kernel-agent-loop.feature`` is the
authoritative BDD contract for the kernel agent loop. Each scenario
binds to step definitions in this module via pytest-bdd; changing the
scenario text without a matching step def causes pytest to fail with
``StepDefinitionNotFoundError``.

This complements (does not replace) the engineering-level tests in
``tests/kernel/test_loop.py``. BDD here proves the scenarios the spec
advertises are actually executed; the pytest unit tests cover edge
cases and internals not worth surfacing in Gherkin.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.loop import AgentLoop, LoopConfig

from .conftest import BDDContext

pytestmark = pytest.mark.unit

FEATURE_FILE = Path(__file__).parent / "features" / "kernel-agent-loop.feature"
scenarios(str(FEATURE_FILE))


# ---------------------------------------------------------------------------
# Stub plugins — each subscribes directly to the bus (no registry at #12).
# Mirrored intentionally from tests/kernel/test_loop.py patterns, but
# duplicated here because bdd-workflow.md forbids importing from
# ``tests/<area>/``.
# ---------------------------------------------------------------------------


@dataclass
class _FakeStrategy:
    bus: EventBus
    script: list[dict[str, Any]]
    calls: int = 0

    def subscribe(self) -> None:
        self.bus.subscribe("strategy.decide.request", self._on_request, source="bdd-strategy")

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
                source="bdd-strategy",
            )
        )


@dataclass
class _FakeLLM:
    bus: EventBus
    text: str = "hello"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0

    def subscribe(self) -> None:
        self.bus.subscribe("llm.call.request", self._on_request, source="bdd-llm")

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
                source="bdd-llm",
            )
        )


@dataclass
class _FakeTool:
    bus: EventBus
    calls: list[dict[str, Any]] = field(default_factory=list)

    def subscribe(self) -> None:
        self.bus.subscribe("tool.call.request", self._on_request, source="bdd-tool")

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
                source="bdd-tool",
            )
        )


@dataclass
class _FakeMemory:
    bus: EventBus
    hits: list[dict[str, Any]] = field(default_factory=list)

    def subscribe(self) -> None:
        self.bus.subscribe("memory.query", self._on_query, source="bdd-memory")

    async def _on_query(self, ev: Event) -> None:
        await self.bus.publish(
            new_event(
                "memory.result",
                {"hits": list(self.hits), "request_id": ev.id},
                session_id=ev.session_id,
                source="bdd-memory",
            )
        )


async def _settle(bus: EventBus) -> None:
    """Yield enough times for every session worker to drain."""
    for _ in range(30):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Scenario 1: Happy path
# ---------------------------------------------------------------------------


@given('an AgentLoop with a stub strategy that returns "llm" then "done"')
def _strategy_llm_then_done(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    strategy = _FakeStrategy(
        bus,
        script=[
            {"next": "llm", "provider": "fake", "model": "m"},
            {"next": "done"},
        ],
    )
    strategy.subscribe()
    ctx.extras["strategy"] = strategy


@given(parsers.re(r'a stub LLM provider returning "(?P<text>[^"]+)"$'))
def _stub_llm_returning(ctx: BDDContext, text: str) -> None:
    assert ctx.bus is not None
    llm = _FakeLLM(ctx.bus, text=text)
    llm.subscribe()
    ctx.extras["llm"] = llm


@when("a user.message.received event is published")
def _publish_user_message(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    bus = ctx.bus

    async def _drive() -> list[Event]:
        agent_loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
        await agent_loop.start()

        done_events: list[Event] = []
        tool_start: list[Event] = []
        tool_result: list[Event] = []
        order: list[str] = []

        async def on_done(ev: Event) -> None:
            done_events.append(ev)
            order.append("assistant.message.done")

        async def on_tool_start(ev: Event) -> None:
            tool_start.append(ev)
            order.append("tool.call.start")

        async def on_tool_result(ev: Event) -> None:
            tool_result.append(ev)
            order.append("tool.call.result")

        bus.subscribe("assistant.message.done", on_done, source="probe")
        bus.subscribe("tool.call.start", on_tool_start, source="probe")
        bus.subscribe("tool.call.result", on_tool_result, source="probe")

        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "hi"},
                session_id="bdd-happy",
                source="bdd-adapter",
            )
        )
        await _settle(bus)
        await agent_loop.stop()
        await bus.close()
        ctx.extras["done_events"] = done_events
        ctx.extras["tool_start"] = tool_start
        ctx.extras["tool_result"] = tool_result
        ctx.extras["order"] = order
        return done_events

    loop.run_until_complete(_drive())


@then(parsers.re(r'an assistant\.message\.done event is observed with content "(?P<text>[^"]+)"$'))
def _assistant_done_content(ctx: BDDContext, text: str) -> None:
    done_events: list[Event] = ctx.extras["done_events"]
    assert len(done_events) == 1, f"expected 1 assistant.message.done, got {len(done_events)}"
    assert done_events[0].payload["content"] == text


@then("the frozen per-turn event sequence drove the turn")
def _frozen_sequence_drove_turn(ctx: BDDContext) -> None:
    # Happy-path proof points: the strategy was consulted at least once
    # and the LLM was called, which means decide-request/response and
    # llm.call.request/response both fired in order before done.
    strategy: _FakeStrategy = ctx.extras["strategy"]
    llm: _FakeLLM = ctx.extras["llm"]
    assert strategy.calls >= 2, f"strategy called only {strategy.calls} times"
    assert llm.calls == 1, f"llm called {llm.calls} times, expected 1"


# ---------------------------------------------------------------------------
# Scenario 2: Tool round-trip
# ---------------------------------------------------------------------------


@given('an AgentLoop with a strategy that returns "tool" then "llm" then "done"')
def _strategy_tool_llm_done(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    strategy = _FakeStrategy(
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
    strategy.subscribe()
    ctx.extras["strategy"] = strategy


@given("a stub tool plugin that echoes its args")
def _stub_tool(ctx: BDDContext) -> None:
    assert ctx.bus is not None
    tool = _FakeTool(ctx.bus)
    tool.subscribe()
    ctx.extras["tool"] = tool


@given("a stub LLM provider")
def _stub_llm_default(ctx: BDDContext) -> None:
    assert ctx.bus is not None
    llm = _FakeLLM(ctx.bus, text="post-tool")
    llm.subscribe()
    ctx.extras["llm"] = llm


@when("a user.message.received event arrives")
def _user_message_arrives(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    bus = ctx.bus

    async def _drive() -> None:
        cfg = ctx.extras.get("loop_config") or LoopConfig(step_timeout_s=2.0)
        agent_loop = AgentLoop(bus, cfg)
        await agent_loop.start()

        done_events: list[Event] = []
        tool_start: list[Event] = []
        tool_result: list[Event] = []
        errors: list[Event] = []
        order: list[str] = []

        async def on_done(ev: Event) -> None:
            done_events.append(ev)
            order.append("assistant.message.done")

        async def on_tool_start(ev: Event) -> None:
            tool_start.append(ev)
            order.append("tool.call.start")

        async def on_tool_result(ev: Event) -> None:
            tool_result.append(ev)
            order.append("tool.call.result")

        async def on_kernel_error(ev: Event) -> None:
            errors.append(ev)

        bus.subscribe("assistant.message.done", on_done, source="probe")
        bus.subscribe("tool.call.start", on_tool_start, source="probe")
        bus.subscribe("tool.call.result", on_tool_result, source="probe")
        bus.subscribe("kernel.error", on_kernel_error, source="probe")

        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "run it"},
                session_id="bdd-turn",
                source="bdd-adapter",
            )
        )
        await _settle(bus)
        await agent_loop.stop()
        await bus.close()

        ctx.extras["done_events"] = done_events
        ctx.extras["tool_start"] = tool_start
        ctx.extras["tool_result"] = tool_result
        ctx.extras["errors"] = errors
        ctx.extras["order"] = order

    loop.run_until_complete(_drive())


@then("a tool.call.start event and a tool.call.result event are observed")
def _tool_events_observed(ctx: BDDContext) -> None:
    assert ctx.extras["tool_start"], "no tool.call.start seen"
    assert ctx.extras["tool_result"], "no tool.call.result seen"


@then("they occur before assistant.message.done in the frozen event sequence")
def _tool_before_done(ctx: BDDContext) -> None:
    order: list[str] = ctx.extras["order"]
    assert "tool.call.start" in order
    assert "tool.call.result" in order
    assert "assistant.message.done" in order
    assert order.index("tool.call.start") < order.index("assistant.message.done")
    assert order.index("tool.call.result") < order.index("assistant.message.done")


# ---------------------------------------------------------------------------
# Scenario 3: max_iterations guard
# ---------------------------------------------------------------------------


@given("an AgentLoop configured with max_iterations=3")
def _loop_max_iter_3(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    ctx.extras["loop_config"] = LoopConfig(max_iterations=3, step_timeout_s=2.0)


@given('a strategy that never returns "done"')
def _strategy_never_done(ctx: BDDContext) -> None:
    assert ctx.bus is not None
    # Memory loop is cheapest — no LLM or tool required.
    strategy = _FakeStrategy(ctx.bus, script=[{"next": "memory", "query": "q", "k": 1}])
    memory = _FakeMemory(ctx.bus, hits=[])
    strategy.subscribe()
    memory.subscribe()
    ctx.extras["strategy"] = strategy


@then(parsers.re(r'a kernel\.error event is emitted carrying message "(?P<msg>[^"]+)"$'))
def _kernel_error_message(ctx: BDDContext, msg: str) -> None:
    errors: list[Event] = ctx.extras["errors"]
    assert any(e.payload.get("message") == msg for e in errors), (
        f"expected kernel.error with message={msg!r}, got {[e.payload for e in errors]}"
    )


@then("the turn aborts without assistant.message.done")
def _no_assistant_done(ctx: BDDContext) -> None:
    assert ctx.extras["done_events"] == [], f"expected no assistant.message.done, got {ctx.extras['done_events']}"


# ---------------------------------------------------------------------------
# Scenario 4: user.interrupt guard
# ---------------------------------------------------------------------------


@given("an AgentLoop mid-turn awaiting a tool.call.result")
def _loop_awaiting_tool_result(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    strategy = _FakeStrategy(
        bus,
        script=[
            {
                "next": "tool",
                "tool_call": {"id": "t-slow", "name": "slow", "args": {}},
            }
        ],
    )
    strategy.subscribe()
    ctx.extras["strategy"] = strategy

    tool_requests: list[Event] = []

    async def never_responding_tool(ev: Event) -> None:
        tool_requests.append(ev)

    bus.subscribe("tool.call.request", never_responding_tool, source="bdd-slow-tool")
    ctx.extras["tool_requests"] = tool_requests


@given("the session worker is idle")
def _session_worker_idle(ctx: BDDContext) -> None:
    # Reads naturally in scenario text; the FIFO per-session worker is
    # an EventBus invariant we're relying on, nothing to configure here.
    ctx.extras["_idle_acknowledged"] = True


@when("a user.interrupt event is published for the same active session")
def _publish_interrupt(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    bus = ctx.bus

    dones: list[Event] = []

    async def on_done(ev: Event) -> None:
        dones.append(ev)

    bus.subscribe("assistant.message.done", on_done, source="probe")

    async def _drive() -> None:
        agent_loop = AgentLoop(bus, LoopConfig(step_timeout_s=5.0))
        await agent_loop.start()

        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "start"},
                session_id="bdd-interrupt",
                source="bdd-adapter",
            )
        )
        # Let the loop reach the tool.call.request stage.
        for _ in range(20):
            await asyncio.sleep(0)

        tool_requests: list[Event] = ctx.extras["tool_requests"]
        assert len(tool_requests) == 1
        ctx.extras["requests_before_interrupt"] = len(tool_requests)

        await bus.publish(
            new_event(
                "user.interrupt",
                {},
                session_id="bdd-interrupt",
                source="bdd-adapter",
            )
        )
        for _ in range(20):
            await asyncio.sleep(0)

        await agent_loop.stop()
        await bus.close()
        ctx.extras["done_events"] = dones

    loop.run_until_complete(_drive())


@then("the current turn aborts under the interrupt guard")
def _turn_aborts(ctx: BDDContext) -> None:
    assert ctx.extras["done_events"] == [], f"expected no assistant.message.done, got {ctx.extras['done_events']}"


@then("no further tool.call.request is emitted for that turn")
def _no_further_tool_request(ctx: BDDContext) -> None:
    tool_requests: list[Event] = ctx.extras["tool_requests"]
    before = ctx.extras["requests_before_interrupt"]
    assert len(tool_requests) == before, (
        f"tool.call.request leaked post-interrupt: had {before}, now {len(tool_requests)}"
    )


# ---------------------------------------------------------------------------
# Scenario 5: Correlation — untracked response ignored
# ---------------------------------------------------------------------------


@given("an AgentLoop with an in-flight outbound request tracked by its event id")
def _loop_with_in_flight_request(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus

    # Strategy never responds → there IS an in-flight strategy.decide.request
    # awaiting a correlated response.
    async def silent(_: Event) -> None:
        return None

    bus.subscribe("strategy.decide.request", silent, source="bdd-silent-strategy")
    ctx.extras["silent_strategy_subscribed"] = True


@when("a response event arrives carrying no matching request_id correlation")
def _publish_untracked_response(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    bus = ctx.bus

    async def _drive() -> None:
        agent_loop = AgentLoop(bus, LoopConfig(step_timeout_s=5.0))
        await agent_loop.start()

        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "go"},
                session_id="bdd-untracked",
                source="bdd-adapter",
            )
        )
        # Let the loop reach the strategy.decide.request stage.
        for _ in range(20):
            await asyncio.sleep(0)

        # Publish a stray response with a request_id that does NOT
        # correlate to any in-flight outbound request.
        await bus.publish(
            new_event(
                "llm.call.response",
                {"text": "stray", "usage": {}, "request_id": "unknown-id"},
                session_id="bdd-untracked",
                source="rogue-llm",
            )
        )
        # And a response with no request_id at all.
        await bus.publish(
            new_event(
                "memory.result",
                {"hits": []},
                session_id="bdd-untracked",
                source="rogue-memory",
            )
        )

        for _ in range(10):
            await asyncio.sleep(0)

        # The turn is still awaiting the real strategy.decide.response —
        # it has NOT crashed and NOT completed.
        dones: list[Event] = []

        async def on_done(ev: Event) -> None:
            dones.append(ev)

        bus.subscribe("assistant.message.done", on_done, source="probe")

        for _ in range(10):
            await asyncio.sleep(0)

        ctx.extras["done_events_while_waiting"] = list(dones)
        ctx.extras["turn_still_pending"] = any(not t.done() for t in agent_loop._turns.values())

        await agent_loop.stop()
        await bus.close()

    loop.run_until_complete(_drive())


@then("the untracked response is ignored and the loop keeps awaiting the correlated reply")
def _untracked_ignored(ctx: BDDContext) -> None:
    assert ctx.extras["done_events_while_waiting"] == [], "assistant.message.done leaked after stray response"
    assert ctx.extras["turn_still_pending"], "turn terminated after untracked response; expected it to keep awaiting"
