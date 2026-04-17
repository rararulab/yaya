"""Tests for EventBus: delivery, isolation, extension routing, timeout, FIFO."""

from __future__ import annotations

import asyncio
import logging

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
        # Variable delay to try to reorder — the per-session worker must preserve order.
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


async def test_handler_can_emit_on_same_session() -> None:
    """A handler re-publishing on its own session must not deadlock.

    Regression for the per-session Lock design: acquiring the same
    ``asyncio.Lock`` inside a handler hung and spuriously surfaced a
    ``plugin.error``. With the queue-based design the follow-up event
    is enqueued and drained after the current handler returns.
    """
    bus = EventBus()
    results: list[Event] = []
    errors: list[Event] = []

    async def request_handler(ev: Event) -> None:
        await bus.publish(
            new_event(
                "tool.call.result",
                {"id": ev.payload["id"], "ok": True, "value": 42},
                session_id=ev.session_id,
                source="tool.demo",
            )
        )

    async def on_result(ev: Event) -> None:
        results.append(ev)

    async def on_error(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("tool.call.request", request_handler, source="tool.demo")
    bus.subscribe("tool.call.result", on_result, source="observer")
    bus.subscribe("plugin.error", on_error, source="observer")

    await bus.publish(
        new_event(
            "tool.call.request",
            {"id": "call-1", "name": "demo", "args": {}},
            session_id="s-1",
            source="kernel",
        )
    )
    # Let the session worker drain the follow-up event.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(results) == 1
    assert results[0].payload["id"] == "call-1"
    assert errors == []


async def test_session_queue_releases_when_idle() -> None:
    """After a burst of unique session_ids, no queue/worker entries leak."""
    bus = EventBus()

    async def handler(_: Event) -> None:
        return None

    bus.subscribe("x.burst.tick", handler, source="burst")

    async def one(n: int) -> None:
        await bus.publish(new_event("x.burst.tick", {"n": n}, session_id=f"s-{n}", source="gen"))

    await asyncio.gather(*(one(i) for i in range(1000)))
    # Give pending worker coroutines a chance to finalise their ``finally``
    # cleanup block.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(bus._session_queues) == 0
    assert len(bus._session_workers) == 0


async def test_cancellation_propagates_without_plugin_error() -> None:
    """Cancelling an outer publish must not be swallowed as plugin.error."""
    bus = EventBus()
    errors: list[Event] = []
    started = asyncio.Event()

    async def slow(_: Event) -> None:
        started.set()
        await asyncio.sleep(10)

    async def on_err(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("user.message.received", slow, source="slow")
    bus.subscribe("plugin.error", on_err, source="observer")

    task = asyncio.create_task(
        bus.publish(new_event("user.message.received", {"text": "x"}, session_id="s", source="a"))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Allow any pending error-emit path to run (there should be none).
    for _ in range(3):
        await asyncio.sleep(0)
    assert errors == []


async def test_duplicate_subscription_same_args_unsubscribes_one_copy() -> None:
    """Subscribing the same handler+source twice yields two distinct entries."""
    bus = EventBus()
    count = 0

    async def handler(_: Event) -> None:
        nonlocal count
        count += 1

    sub_a = bus.subscribe("kernel.ready", handler, source="t")
    bus.subscribe("kernel.ready", handler, source="t")
    sub_a.unsubscribe()

    await bus.publish(new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel"))
    assert count == 1


async def test_cross_session_cycle_does_not_deadlock() -> None:
    """Handlers on different sessions that publish into each other must not deadlock.

    With per-session workers AND await-done-for-external-callers, a cycle
    where s1 handler publishes to s2 and s2 handler publishes to s1 would
    block both workers on each other's completion future. The fix is to
    treat any in-worker caller as fire-and-forget, regardless of target
    session.
    """
    bus = EventBus(handler_timeout_s=1.0)
    seen_pong: list[Event] = []

    async def on_ping(ev: Event) -> None:
        # Received on s2; publishes a pong to s1.
        await bus.publish(new_event("x.pong", {"reply": ev.payload["n"]}, session_id="s1", source="p"))

    async def on_pong(ev: Event) -> None:
        seen_pong.append(ev)

    bus.subscribe("x.ping", on_ping, source="p")
    bus.subscribe("x.pong", on_pong, source="observer")

    # External call: top-level task publishes a ping to s2 and awaits delivery.
    await asyncio.wait_for(
        bus.publish(new_event("x.ping", {"n": 1}, session_id="s2", source="kernel")),
        timeout=0.5,
    )
    # Let s1's worker drain the pong.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(seen_pong) == 1
    assert seen_pong[0].payload["reply"] == 1


async def test_plugin_error_cascade_does_not_deadlock() -> None:
    """A plugin.error handler that publishes back to the originating session must not deadlock.

    Regression for the round-1 design: _report_handler_failure awaited the
    kernel-session done from inside a session worker, so a plugin.error
    handler that cascaded back to the originating session would hang.
    """
    bus = EventBus(handler_timeout_s=1.0)
    cascade: list[Event] = []

    async def bad(_: Event) -> None:
        raise RuntimeError("boom")

    async def on_error_cascade(_: Event) -> None:
        await bus.publish(new_event("x.echo", {"msg": "after-error"}, session_id="s1", source="errhandler"))

    async def on_echo(ev: Event) -> None:
        cascade.append(ev)

    bus.subscribe("x.trigger", bad, source="bad")
    bus.subscribe("plugin.error", on_error_cascade, source="errhandler")
    bus.subscribe("x.echo", on_echo, source="observer")

    await asyncio.wait_for(
        bus.publish(new_event("x.trigger", {}, session_id="s1", source="kernel")),
        timeout=0.5,
    )
    # Allow kernel + s1 workers to drain.
    for _ in range(10):
        await asyncio.sleep(0)

    assert len(cascade) == 1
    assert cascade[0].payload["msg"] == "after-error"


async def test_publish_after_close_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Publishing after close is a no-op but surfaces a warning with the kind."""
    bus = EventBus()
    await bus.close()
    with caplog.at_level(logging.WARNING, logger="yaya.kernel.bus"):
        await bus.publish(new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel"))
    assert any("kernel.ready" in rec.getMessage() for rec in caplog.records)
