"""Pytest-bdd execution of specs/kernel-cross-turn-history.spec scenarios.

The Gherkin text in ``features/kernel-cross-turn-history.feature`` is
the authoritative BDD contract for #156. Each scenario binds to step
definitions in this module via pytest-bdd; changing the scenario text
without a matching step def causes pytest to fail with
``StepDefinitionNotFoundError``.

The engineering-level tests in ``tests/kernel/test_loop.py`` cover
edge cases (persister race, store failure) that are not worth
surfacing in Gherkin.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.loop import AgentLoop, LoopConfig

from .conftest import BDDContext

pytestmark = pytest.mark.unit

FEATURE_FILE = Path(__file__).parent / "features" / "kernel-cross-turn-history.feature"
scenarios(str(FEATURE_FILE))


# ---------------------------------------------------------------------------
# Stubs mirrored intentionally from tests/kernel/test_loop.py patterns.
# bdd-workflow.md forbids importing from tests/<area>/ so the duplication
# is deliberate: BDD tests stay self-contained.
# ---------------------------------------------------------------------------


@dataclass
class _FakeEntry:
    """Tape entry duck — republic ships ``kind`` / ``payload`` on every row."""

    kind: str
    payload: dict[str, Any]


@dataclass
class _FakeSession:
    """Returns pre-scripted entries instead of touching the real tape store."""

    scripted: list[Any]

    async def entries(self) -> list[Any]:
        return list(self.scripted)


@dataclass
class _FakeStore:
    """``SessionStore`` duck: ``open(workspace, session_id) -> Session``."""

    session: _FakeSession

    async def open(self, _workspace: Any, _session_id: str) -> _FakeSession:
        return self.session


@dataclass
class _DecisionRecorder:
    """Scripted strategy that records the messages it sees and says ``done``."""

    bus: EventBus
    observed: list[list[dict[str, Any]]] = field(default_factory=list)

    def subscribe(self) -> None:
        self.bus.subscribe("strategy.decide.request", self._on_request, source="bdd-strategy")

    async def _on_request(self, ev: Event) -> None:
        state = ev.payload.get("state", {})
        msgs = state.get("messages", []) if isinstance(state, dict) else []
        self.observed.append(list(msgs))
        await self.bus.publish(
            new_event(
                "strategy.decide.response",
                {"next": "done", "request_id": ev.id},
                session_id=ev.session_id,
                source="bdd-strategy",
            )
        )


async def _settle(bus: EventBus) -> None:
    for _ in range(20):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Scenario 1: prior messages load.
# ---------------------------------------------------------------------------


@given("a session tape with one completed user/assistant exchange")
def _seed_exchange_tape(ctx: BDDContext) -> None:
    ctx.extras["tape"] = [
        _FakeEntry("anchor", {"name": "session/start", "state": {"owner": "human"}}),
        _FakeEntry("message", {"role": "user", "content": "hi"}),
        _FakeEntry("message", {"role": "assistant", "content": "hello"}),
    ]


@given("an AgentLoop wired to the session store for that workspace")
def _wire_loop_with_store(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    recorder = _DecisionRecorder(bus)
    recorder.subscribe()
    store = _FakeStore(_FakeSession(ctx.extras["tape"]))
    loop = AgentLoop(
        bus,
        LoopConfig(step_timeout_s=2.0),
        session_store=store,
        workspace="/fake/ws",
    )
    ctx.extras["loop"] = loop
    ctx.extras["recorder"] = recorder


@when("a second user.message.received event is published on the same session")
def _publish_second_message(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    loop.run_until_complete(_drive_single_turn(ctx, "what did I say?", "bdd-hist"))


@then(
    "the strategy.decide.request carries the prior user message, "
    "the prior assistant reply, and the new user message in order"
)
def _assert_three_message_history(ctx: BDDContext) -> None:
    recorder: _DecisionRecorder = ctx.extras["recorder"]
    assert recorder.observed == [
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "what did I say?"},
        ]
    ]


# ---------------------------------------------------------------------------
# Scenario 2: compaction elision.
# ---------------------------------------------------------------------------


@given("a session tape with two pre-compaction messages followed by a compaction anchor and one post-anchor message")
def _seed_compaction_tape(ctx: BDDContext) -> None:
    ctx.extras["tape"] = [
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


@when("a new user.message.received event arrives on the same session")
def _publish_new_message(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    loop.run_until_complete(_drive_single_turn(ctx, "follow up", "bdd-compact"))


@then(
    "the strategy.decide.request omits the pre-anchor messages and "
    "starts with the compaction summary as a system message"
)
def _assert_compaction_elision(ctx: BDDContext) -> None:
    recorder: _DecisionRecorder = ctx.extras["recorder"]
    assert recorder.observed == [
        [
            {"role": "system", "content": "[compacted history]\nprior chat summarised"},
            {"role": "user", "content": "msg-C"},
            {"role": "user", "content": "follow up"},
        ]
    ]


# ---------------------------------------------------------------------------
# Scenario 3: no-store fallback.
# ---------------------------------------------------------------------------


@given("an AgentLoop constructed without a session store")
def _wire_loop_without_store(ctx: BDDContext) -> None:
    bus = EventBus()
    ctx.bus = bus
    recorder = _DecisionRecorder(bus)
    recorder.subscribe()
    loop = AgentLoop(bus, LoopConfig(step_timeout_s=2.0))
    ctx.extras["loop"] = loop
    ctx.extras["recorder"] = recorder


@when("a user.message.received event arrives")
def _publish_solo_message(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    loop.run_until_complete(_drive_single_turn(ctx, "solo", "bdd-solo"))


@then("the strategy.decide.request carries only the incoming user message")
def _assert_solo_message(ctx: BDDContext) -> None:
    recorder: _DecisionRecorder = ctx.extras["recorder"]
    assert recorder.observed == [[{"role": "user", "content": "solo"}]]


# ---------------------------------------------------------------------------
# Shared turn driver.
# ---------------------------------------------------------------------------


async def _drive_single_turn(ctx: BDDContext, text: str, session_id: str) -> None:
    loop: AgentLoop = ctx.extras["loop"]
    bus = ctx.bus
    assert bus is not None
    await loop.start()
    await bus.publish(
        new_event(
            "user.message.received",
            {"text": text},
            session_id=session_id,
            source="bdd-adapter",
        )
    )
    await _settle(bus)
    await loop.stop()
    await bus.close()
