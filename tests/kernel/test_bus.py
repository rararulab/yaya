"""Tests for EventBus: delivery, isolation, extension routing, timeout, FIFO."""

from __future__ import annotations

import asyncio

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event


async def test_delivers_to_subscriber() -> None:
    """AC-01 — a published event reaches an exact-kind subscriber with envelope fields."""
    bus = EventBus()
    received: list[Event] = []

    async def handler(ev: Event) -> None:
        received.append(ev)

    bus.subscribe("user.message.received", handler, source="test")
    ev = new_event("user.message.received", {"text": "hi"}, session_id="s-1", source="adapter")
    await bus.publish(ev)

    assert len(received) == 1
    got = received[0]
    assert got.id == ev.id
    assert got.kind == "user.message.received"
    assert got.session_id == "s-1"
    assert got.source == "adapter"
    assert got.ts > 0
    assert got.payload == {"text": "hi"}


async def test_raising_subscriber_isolated() -> None:
    """AC-02 — a raising handler does not stop other subscribers and triggers plugin.error."""
    bus = EventBus()
    healthy: list[Event] = []
    errors: list[Event] = []

    async def bad(_: Event) -> None:
        raise RuntimeError("boom")

    async def good(ev: Event) -> None:
        healthy.append(ev)

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("user.message.received", bad, source="bad-plugin")
    bus.subscribe("user.message.received", good, source="good-plugin")
    bus.subscribe("plugin.error", on_err, source="observer")

    await bus.publish(new_event("user.message.received", {"text": "x"}, session_id="s", source="adapter"))

    assert len(healthy) == 1
    assert len(errors) == 1
    err = errors[0]
    assert err.kind == "plugin.error"
    assert err.source == "kernel"
    assert err.payload == {"name": "bad-plugin", "error": "boom"}


async def test_extension_namespace_routes() -> None:
    """AC-04 — x.* events route opaquely; payload passes through untouched."""
    bus = EventBus()
    received: list[Event] = []

    async def handler(ev: Event) -> None:
        received.append(ev)

    bus.subscribe("x.foo.bar", handler, source="foo")
    payload = {"deeply": {"nested": [1, 2, 3]}, "unknown": object()}
    ev = new_event("x.foo.bar", payload, session_id="s", source="foo")
    await bus.publish(ev)

    assert len(received) == 1
    assert received[0].payload is payload


async def test_subscriber_timeout_triggers_plugin_error() -> None:
    """A handler exceeding the configured deadline is cancelled and reported."""
    bus = EventBus(handler_timeout_s=0.05)
    errors: list[Event] = []

    async def slow(_: Event) -> None:
        await asyncio.sleep(10)

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("user.message.received", slow, source="slowpoke")
    bus.subscribe("plugin.error", on_err, source="observer")

    await bus.publish(new_event("user.message.received", {"text": "x"}, session_id="s", source="adapter"))

    assert len(errors) == 1
    assert errors[0].payload["name"] == "slowpoke"


async def test_fifo_per_session() -> None:
    """Events sharing a session_id are delivered in publish order."""
    bus = EventBus()
    seen: list[int] = []

    async def handler(ev: Event) -> None:
        # Variable delay to try to reorder — the per-session lock must prevent it.
        await asyncio.sleep(0.01 if ev.payload["n"] % 2 == 0 else 0.001)
        seen.append(ev.payload["n"])

    bus.subscribe("x.fifo.tick", handler, source="fifo")

    async def publish(n: int) -> None:
        await bus.publish(new_event("x.fifo.tick", {"n": n}, session_id="same", source="gen"))

    await asyncio.gather(*(publish(i) for i in range(10)))

    assert seen == sorted(seen)  # strictly monotonic per publish order within the session.
    assert len(seen) == 10


async def test_unsubscribe_stops_delivery() -> None:
    """Subscription.unsubscribe() removes the handler from the routing table."""
    bus = EventBus()
    received: list[Event] = []

    async def handler(ev: Event) -> None:
        received.append(ev)

    sub = bus.subscribe("kernel.ready", handler, source="t")
    sub.unsubscribe()
    # Idempotent.
    sub.unsubscribe()
    await bus.publish(new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel"))
    assert received == []


async def test_close_is_idempotent_and_blocks_publish() -> None:
    """Close drains state and becomes a no-op on subsequent publishes."""
    bus = EventBus()
    received: list[Event] = []

    async def handler(ev: Event) -> None:
        received.append(ev)

    bus.subscribe("kernel.ready", handler, source="t")
    await bus.close()
    await bus.close()
    await bus.publish(new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel"))
    assert received == []


async def test_kernel_origin_failure_does_not_recurse() -> None:
    """A handler that raises with source='kernel' must not spawn another plugin.error."""
    bus = EventBus()
    err_count = 0

    async def raising_kernel_handler(_: Event) -> None:
        raise RuntimeError("kernel internal")

    async def on_err(_: Event) -> None:
        nonlocal err_count
        err_count += 1

    bus.subscribe("plugin.error", raising_kernel_handler, source="kernel")
    bus.subscribe("plugin.error", on_err, source="observer")

    # Publish a plugin.error directly.
    ev = new_event(
        "plugin.error",
        {"name": "foo", "error": "bar"},
        session_id="kernel",
        source="kernel",
    )
    await bus.publish(ev)

    # The observer saw the original; the raising kernel handler did NOT cause another.
    assert err_count == 1


def test_handler_timeout_default_is_30s() -> None:
    """Default timeout matches the plugin-protocol 30s deadline."""
    from yaya.kernel.bus import DEFAULT_HANDLER_TIMEOUT_S

    assert pytest.approx(30.0) == DEFAULT_HANDLER_TIMEOUT_S
