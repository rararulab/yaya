"""Pytest-bdd execution of specs/kernel-bus-and-abi.spec scenarios.

The Gherkin text in ``features/kernel-bus-and-abi.feature`` is the
authoritative BDD contract for the kernel event bus. Each scenario
binds to step definitions in this module via pytest-bdd; changing the
scenario text without a matching step def causes pytest to fail with
``StepDefinitionNotFoundError``.

This complements (does not replace) the engineering-level tests in
``tests/kernel/test_bus.py``. BDD here proves the scenarios the spec
advertises are actually executed; the pytest unit tests cover edge
cases and internals not worth surfacing in Gherkin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from yaya.kernel.events import Event, new_event

from .conftest import BDDContext

# Mark the whole module as a unit-level BDD suite.
pytestmark = pytest.mark.unit

FEATURE_FILE = Path(__file__).parent / "features" / "kernel-bus-and-abi.feature"
scenarios(str(FEATURE_FILE))


# -- Scenario 1 -------------------------------------------------------------


@given(
    parsers.re(r'a subscriber registered for "(?P<kind>[\w.]+)"$'),
    target_fixture="_kind",
)
def _subscriber_for(ctx: BDDContext, kind: str) -> str:
    assert ctx.bus is not None
    ctx.received.setdefault(kind, [])

    async def handler(ev: Event) -> None:
        ctx.received[kind].append(ev)

    ctx.handlers[kind] = handler
    ctx.bus.subscribe(kind, handler, source="test-bdd")
    return kind


@when(parsers.re(r'a "(?P<kind>[\w.]+)" event is published$'))
def _publish_basic(ctx: BDDContext, kind: str, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    ev = new_event(kind, {"text": "hi"}, session_id="bdd-session", source="adapter")
    ctx.published_events.append(ev)
    loop.run_until_complete(ctx.bus.publish(ev))


@then("the subscriber receives the event with envelope fields populated")
def _received_populated(ctx: BDDContext) -> None:
    kind = ctx.published_events[-1].kind
    bucket = ctx.received.get(kind, [])
    assert len(bucket) == 1, f"expected 1 delivery for {kind}, got {len(bucket)}"
    got = bucket[0]
    expected = ctx.published_events[-1]
    assert got.id == expected.id
    assert got.kind == expected.kind
    assert got.session_id == expected.session_id
    assert got.source == expected.source
    assert got.ts > 0
    assert got.payload == expected.payload


# -- Scenario 2 (error path, raising subscriber) ---------------------------


@given("a subscriber that raises on receipt")
def _raising_subscriber(ctx: BDDContext) -> None:
    assert ctx.bus is not None

    async def bad(_: Event) -> None:
        raise RuntimeError("bdd-bad-handler")

    ctx.handlers["_raising"] = bad
    # Subscribe to the default kind used downstream; scenario uses
    # "user.message.received" as the implicit kind for the pair.
    ctx.bus.subscribe("user.message.received", bad, source="bdd-bad")


@given("a second healthy subscriber for the same kind")
def _healthy_pair(ctx: BDDContext) -> None:
    assert ctx.bus is not None
    ctx.received.setdefault("user.message.received", [])

    async def good(ev: Event) -> None:
        ctx.received["user.message.received"].append(ev)

    async def on_err(ev: Event) -> None:
        ctx.errors.append(ev)

    ctx.handlers["_good"] = good
    ctx.handlers["_on_err"] = on_err
    ctx.bus.subscribe("user.message.received", good, source="bdd-good")
    ctx.bus.subscribe("plugin.error", on_err, source="bdd-errlisten")


@when("the event is published")
def _publish_default_kind(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    ev = new_event(
        "user.message.received",
        {"text": "hi"},
        session_id="bdd-session",
        source="adapter",
    )
    ctx.published_events.append(ev)
    loop.run_until_complete(ctx.bus.publish(ev))


@then("the healthy subscriber still receives the event")
def _healthy_received(ctx: BDDContext) -> None:
    bucket = ctx.received.get("user.message.received", [])
    assert len(bucket) == 1


@then('a synthetic "plugin.error" event is emitted by the bus')
def _plugin_error_emitted(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    # Give the synthetic emit a chance to complete on the loop.
    loop.run_until_complete(asyncio.sleep(0))
    assert any(e.kind == "plugin.error" for e in ctx.errors), "expected a plugin.error, got: " + ", ".join(
        e.kind for e in ctx.errors
    )


# -- Scenario 3 (envelope fields) ------------------------------------------


@given("the events module")
def _events_module(ctx: BDDContext) -> None:
    # Nothing to set up; step exists so the Given reads naturally.
    ctx.extras["events_module_loaded"] = True


@when("new_event is called with a known public kind")
def _make_event(ctx: BDDContext) -> None:
    ctx.extras["envelope"] = new_event(
        "user.message.received",
        {"text": "hi"},
        session_id="bdd",
        source="adapter",
    )


@then("the returned Event envelope has id, ts, source, session_id, kind, and payload fields")
def _envelope_has_all_fields(ctx: BDDContext) -> None:
    ev: Event = ctx.extras["envelope"]
    assert ev.id
    assert ev.kind == "user.message.received"
    assert ev.session_id == "bdd"
    assert ev.source == "adapter"
    assert ev.ts > 0
    assert ev.payload == {"text": "hi"}


# -- Scenario 4 (extension namespace) --------------------------------------


@given(parsers.re(r'a subscriber for "(?P<kind>x\.[\w.]+)" in the extension namespace$'))
def _extension_subscriber(ctx: BDDContext, kind: str) -> None:
    assert ctx.bus is not None
    ctx.received.setdefault(kind, [])

    async def handler(ev: Event) -> None:
        ctx.received[kind].append(ev)

    ctx.handlers[kind] = handler
    ctx.bus.subscribe(kind, handler, source="bdd-ext")


@when(parsers.re(r'an "(?P<kind>x\.[\w.]+)" event is published with an arbitrary payload$'))
def _publish_extension(ctx: BDDContext, kind: str, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    payload: dict[str, Any] = {"arbitrary": 42, "nested": {"a": [1, 2]}}
    ev = new_event(kind, payload, session_id="bdd", source="ext-plugin")
    ctx.published_events.append(ev)
    loop.run_until_complete(ctx.bus.publish(ev))


@then("the subscriber receives it unchanged without type checks")
def _extension_roundtrip(ctx: BDDContext) -> None:
    kind = ctx.published_events[-1].kind
    bucket = ctx.received.get(kind, [])
    assert len(bucket) == 1
    assert bucket[0].payload == ctx.published_events[-1].payload


# -- Scenario 5 (closed-catalog rejection) ---------------------------------


@given("the events module with its closed public catalog")
def _closed_catalog(ctx: BDDContext) -> None:
    ctx.extras["catalog_ready"] = True


@when(parsers.re(r'new_event is called with unknown public kind "(?P<kind>[\w.]+)"$'))
def _make_unknown_public(ctx: BDDContext, kind: str) -> None:
    try:
        new_event(kind, {}, session_id="bdd", source="adapter")
    except ValueError as exc:
        ctx.extras["raised"] = exc


@then("ValueError is raised referencing the closed public catalog")
def _raised_value_error(ctx: BDDContext) -> None:
    raised = ctx.extras.get("raised")
    assert isinstance(raised, ValueError), f"expected ValueError, got {type(raised).__name__}: {raised!r}"
    assert "catalog" in str(raised).lower() or "unknown" in str(raised).lower()


# -- Scenario 6 (FIFO re-entry) --------------------------------------------


@given("a subscriber that publishes a follow-up event on the same session")
def _reentrant_subscriber(ctx: BDDContext) -> None:
    assert ctx.bus is not None
    seen: list[str] = []

    async def on_first(ev: Event) -> None:
        seen.append("first")
        assert ctx.bus is not None
        follow = new_event(
            "x.followup",
            {"from": ev.id},
            session_id=ev.session_id,
            source="kernel",
        )
        await ctx.bus.publish(follow)

    async def on_follow(_: Event) -> None:
        seen.append("followup")

    ctx.handlers["_first"] = on_first
    ctx.handlers["_follow"] = on_follow
    ctx.bus.subscribe("user.message.received", on_first, source="bdd-first")
    ctx.bus.subscribe("x.followup", on_follow, source="bdd-follow")
    ctx.extras["seen"] = seen


@when("the first event is delivered under the 30 s per-subscriber timeout")
def _publish_first(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    ev = new_event(
        "user.message.received",
        {"text": "go"},
        session_id="bdd-fifo",
        source="adapter",
    )
    ctx.published_events.append(ev)
    loop.run_until_complete(ctx.bus.publish(ev))


@then("the follow-up event is enqueued and delivered in FIFO order without deadlocking")
def _fifo_delivered(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    # Allow the worker to drain both events.
    loop.run_until_complete(asyncio.sleep(0))
    seen = ctx.extras["seen"]
    assert seen == ["first", "followup"], f"got ordering: {seen}"
