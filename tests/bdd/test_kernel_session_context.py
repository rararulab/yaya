"""Pytest-bdd execution of specs/kernel-session-context.spec scenarios.

The Gherkin text in ``features/kernel-session-context.feature`` is
the authoritative BDD contract. Each scenario binds to step
definitions in this module via pytest-bdd; unmatched text fails the
test with ``StepDefinitionNotFoundError`` so the spec text and
scenario implementations stay in lock-step.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from yaya.kernel import (
    Connection,
    ConnectionLimitError,
    Event,
    EventBus,
    MemoryTapeStore,
    Session,
    SessionContext,
    SessionManager,
    SessionStore,
    install_session_manager,
    new_event,
)

scenarios("features/kernel-session-context.feature")


@dataclass
class _Ctx:
    """Per-scenario state container."""

    loop: asyncio.AbstractEventLoop | None = None
    bus: EventBus | None = None
    store: SessionStore | None = None
    workspace: Path | None = None
    session: Session | None = None
    sctx: SessionContext | None = None
    manager: SessionManager | None = None
    conns: list[Connection] = field(default_factory=lambda: [])
    buckets: dict[str, list[Event]] = field(default_factory=lambda: {})
    captured_lifecycle: list[Event] = field(default_factory=lambda: [])
    captured_events: list[Event] = field(default_factory=lambda: [])
    caught_error: Exception | None = None
    live_event: Event | None = None
    clock: dict[str, float] = field(default_factory=lambda: {"t": 1000.0})


@pytest.fixture
def bctx(tmp_path: Path) -> _Ctx:
    loop = asyncio.new_event_loop()
    ctx = _Ctx(loop=loop, workspace=tmp_path / "ws")
    assert ctx.workspace is not None
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    yield ctx
    if ctx.sctx is not None:
        loop.run_until_complete(ctx.sctx.close())
    if ctx.manager is not None:
        loop.run_until_complete(ctx.manager.close())
    if ctx.bus is not None:
        loop.run_until_complete(ctx.bus.close())
    if ctx.store is not None:
        loop.run_until_complete(ctx.store.close())
    loop.close()


def _run(bctx: _Ctx, coro: Any) -> Any:
    assert bctx.loop is not None
    return bctx.loop.run_until_complete(coro)


def _ensure_session(bctx: _Ctx, session_id: str = "s") -> Session:
    if bctx.session is not None:
        return bctx.session
    assert bctx.workspace is not None
    bctx.store = SessionStore(store=MemoryTapeStore())
    bctx.session = _run(bctx, bctx.store.open(bctx.workspace, session_id))
    return bctx.session


def _ensure_bus(bctx: _Ctx) -> EventBus:
    if bctx.bus is None:
        bctx.bus = EventBus()
    return bctx.bus


def _make_send(bctx: _Ctx, label: str) -> Any:
    bucket: list[Event] = []
    bctx.buckets[label] = bucket

    async def _send(ev: Event) -> None:
        bucket.append(ev)

    return _send


# -- AC-01 ------------------------------------------------------------------


@given("a session context with two attached connections A and B")
@given("a session context with two attached connections")
def _ctx_with_two(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    session = _ensure_session(bctx)

    async def _cap(ev: Event) -> None:
        bctx.captured_lifecycle.append(ev)

    bus.subscribe("session.context.detached", _cap, source="test")
    bctx.sctx = SessionContext(session=session, bus=bus)
    bctx.conns.append(_run(bctx, bctx.sctx.attach(_make_send(bctx, "A"), adapter_id="A")))
    bctx.conns.append(_run(bctx, bctx.sctx.attach(_make_send(bctx, "B"), adapter_id="B")))


@when("the manager fans out one event")
@when("the manager fans out one event on the session")
def _fan_one(bctx: _Ctx) -> None:
    assert bctx.sctx is not None and bctx.session is not None
    ev = new_event(
        "user.message.received",
        {"text": "hi"},
        session_id=bctx.session.session_id,
        source="test",
    )
    bctx.live_event = ev
    _run(bctx, bctx.sctx.fanout(ev))


@then("both A and B receive the event exactly once")
def _both_receive_once(bctx: _Ctx) -> None:
    assert bctx.live_event is not None
    assert [e.id for e in bctx.buckets["A"]] == [bctx.live_event.id]
    assert [e.id for e in bctx.buckets["B"]] == [bctx.live_event.id]


# -- AC-02 ------------------------------------------------------------------


@when("one hundred events are fanned out")
def _fan_hundred(bctx: _Ctx) -> None:
    assert bctx.sctx is not None and bctx.session is not None
    for i in range(100):
        ev = new_event(
            "user.message.received",
            {"text": f"m-{i}"},
            session_id=bctx.session.session_id,
            source="test",
        )
        _run(bctx, bctx.sctx.fanout(ev))


@then("both connections observe the same sequence")
def _same_sequence(bctx: _Ctx) -> None:
    a = [e.id for e in bctx.buckets["A"]]
    b = [e.id for e in bctx.buckets["B"]]
    assert a == b
    assert len(a) == 100


# -- AC-03 ------------------------------------------------------------------


@given("a session tape with several entries")
def _session_with_entries(bctx: _Ctx) -> None:
    session = _ensure_session(bctx)
    for i in range(5):
        _run(bctx, session.append_message("user", f"m-{i}"))
    bus = _ensure_bus(bctx)
    bctx.sctx = SessionContext(session=session, bus=bus)


@when("a connection attaches with since entry set to the id of an earlier entry")
def _attach_with_since(bctx: _Ctx) -> None:
    assert bctx.sctx is not None and bctx.session is not None
    entries = _run(bctx, bctx.session.entries())
    mid = entries[len(entries) // 2].id
    bctx.conns.append(
        _run(
            bctx,
            bctx.sctx.attach(
                _make_send(bctx, "replay"),
                adapter_id="reconn",
                since_entry=mid,
            ),
        )
    )
    bctx.buckets["replay_since"] = [Event(id="", kind="", session_id="", ts=0.0, source="", payload={"id": mid})]


@then("the connection receives session replay entry events for every later entry")
def _receives_replay_entries(bctx: _Ctx) -> None:
    since = bctx.buckets["replay_since"][0].payload["id"]
    replay = [e for e in bctx.buckets["replay"] if e.kind == "session.replay.entry"]
    assert replay, "no replay entry events observed"
    for e in replay:
        assert e.payload["entry_id"] > since


@then("a single session replay done event closes the replay")
def _single_done(bctx: _Ctx) -> None:
    dones = [e for e in bctx.buckets["replay"] if e.kind == "session.replay.done"]
    assert len(dones) == 1
    assert bctx.buckets["replay"][-1].kind == "session.replay.done"


# -- AC-04 ------------------------------------------------------------------


@given("a session context configured with max connections equal to two")
def _ctx_cap_two(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    session = _ensure_session(bctx)
    bctx.sctx = SessionContext(session=session, bus=bus, max_connections=2)
    _run(bctx, bctx.sctx.attach(_make_send(bctx, "c1")))
    _run(bctx, bctx.sctx.attach(_make_send(bctx, "c2")))


@when("a third connection attempts to attach")
def _attach_third(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    try:
        _run(bctx, bctx.sctx.attach(_make_send(bctx, "c3")))
    except ConnectionLimitError as exc:
        bctx.caught_error = exc


@then("a connection limit error is raised naming the cap")
def _limit_error(bctx: _Ctx) -> None:
    assert isinstance(bctx.caught_error, ConnectionLimitError)
    assert "2-connection cap" in str(bctx.caught_error)


# -- AC-05 ------------------------------------------------------------------


@given("a session context with a short heartbeat timeout")
def _ctx_short_timeout(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    session = _ensure_session(bctx)

    def now() -> float:
        return bctx.clock["t"]

    bctx.sctx = SessionContext(
        session=session,
        bus=bus,
        heartbeat_timeout_s=30.0,
        now=now,
    )


@given("a connection whose last seen is older than the timeout")
def _stale_connection(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    bctx.bus.subscribe(  # type: ignore[union-attr]
        "session.context.detached",
        lambda ev: _capture(bctx, ev),
        source="test",
    )
    conn = _run(bctx, bctx.sctx.attach(_make_send(bctx, "stale")))
    bctx.conns.append(conn)
    bctx.clock["t"] = 2000.0


async def _capture(bctx: _Ctx, ev: Event) -> None:
    bctx.captured_lifecycle.append(ev)


@when("the reap sweep runs")
def _reap(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    _run(bctx, bctx.sctx._reap_once())
    _run(bctx, asyncio.sleep(0))


@then("the stale connection is detached with reason timeout")
def _stale_detached(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    conn = bctx.conns[0]
    assert conn.id not in {c.id for c in bctx.sctx.connections()}


@then("a session context detached event is emitted")
def _detached_event_emitted(bctx: _Ctx) -> None:
    conn = bctx.conns[0]
    assert any(
        e.payload.get("connection_id") == conn.id and e.payload.get("reason") == "timeout"
        for e in bctx.captured_lifecycle
    )


# -- AC-06 ------------------------------------------------------------------


@given("a bus subscriber listening to session context events on session id kernel")
def _subscribe_lifecycle(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)

    async def _cap(ev: Event) -> None:
        bctx.captured_lifecycle.append(ev)

    bus.subscribe("session.context.attached", _cap, source="test")


@when("a connection attaches to a context for session S")
def _attach_S(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    session = _ensure_session(bctx, session_id="S")
    bctx.sctx = SessionContext(session=session, bus=bus)
    _run(bctx, bctx.sctx.attach(_make_send(bctx, "x"), adapter_id="web"))
    _run(bctx, asyncio.sleep(0))


@then("the subscriber observes the attached event")
def _subscriber_saw(bctx: _Ctx) -> None:
    assert any(e.kind == "session.context.attached" for e in bctx.captured_lifecycle)


@then("the envelope session id is kernel")
def _envelope_kernel(bctx: _Ctx) -> None:
    attached = [e for e in bctx.captured_lifecycle if e.kind == "session.context.attached"]
    assert attached and all(e.session_id == "kernel" for e in attached)


# -- AC-07 ------------------------------------------------------------------


@given("a session context with one healthy connection and one connection whose send always raises")
def _ctx_good_and_bad(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    session = _ensure_session(bctx)
    bctx.sctx = SessionContext(session=session, bus=bus)

    good_bucket: list[Event] = []
    bctx.buckets["good"] = good_bucket

    async def good_send(ev: Event) -> None:
        good_bucket.append(ev)

    async def bad_send(_: Event) -> None:
        raise RuntimeError("transport dead")

    async def _cap(ev: Event) -> None:
        bctx.captured_lifecycle.append(ev)

    bus.subscribe("session.context.detached", _cap, source="test")
    bctx.conns.append(_run(bctx, bctx.sctx.attach(good_send, adapter_id="good")))
    bctx.conns.append(_run(bctx, bctx.sctx.attach(bad_send, adapter_id="bad")))


@then("the healthy connection receives the event")
def _healthy_received(bctx: _Ctx) -> None:
    assert bctx.live_event is not None
    assert any(e.id == bctx.live_event.id for e in bctx.buckets["good"])


@then("the raising connection is detached with reason send failed")
def _bad_detached(bctx: _Ctx) -> None:
    bad = bctx.conns[1]
    _run(bctx, asyncio.sleep(0))
    assert any(
        e.payload.get("connection_id") == bad.id and e.payload.get("reason") == "send_failed"
        for e in bctx.captured_lifecycle
    )


# -- AC-08 ------------------------------------------------------------------


@when("close is awaited")
def _close_called(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    _run(bctx, bctx.sctx.close())
    bctx.sctx = None
    _run(bctx, asyncio.sleep(0))


@then("both connections receive a session context detached event")
def _both_detached(bctx: _Ctx) -> None:
    ids = {e.payload.get("connection_id") for e in bctx.captured_lifecycle}
    for c in bctx.conns:
        assert c.id in ids


@then("the reason is shutdown")
def _reason_shutdown(bctx: _Ctx) -> None:
    ids = {c.id for c in bctx.conns}
    shutdowns = {
        e.payload.get("connection_id") for e in bctx.captured_lifecycle if e.payload.get("reason") == "shutdown"
    }
    assert ids.issubset(shutdowns)


# -- AC-09 ------------------------------------------------------------------


@given("a session manager installed with a user message received subscription")
def _install_manager(bctx: _Ctx) -> None:
    bus = _ensure_bus(bctx)
    assert bctx.workspace is not None
    bctx.store = SessionStore(store=MemoryTapeStore())
    bctx.manager = _run(
        bctx,
        install_session_manager(
            bus=bus,
            store=bctx.store,
            workspace=bctx.workspace,
            kinds=["user.message.received"],
        ),
    )


@given("a connection attached for session default")
def _attach_default(bctx: _Ctx) -> None:
    assert bctx.manager is not None
    bctx.conns.append(
        _run(
            bctx,
            bctx.manager.attach("default", _make_send(bctx, "default"), adapter_id="web"),
        )
    )


@when("a user message received event is published on session default")
def _publish_default(bctx: _Ctx) -> None:
    assert bctx.bus is not None
    ev = new_event(
        "user.message.received",
        {"text": "hi"},
        session_id="default",
        source="test",
    )
    bctx.live_event = ev
    _run(bctx, bctx.bus.publish(ev))


@then("the connection receives the same event")
def _conn_received(bctx: _Ctx) -> None:
    assert bctx.live_event is not None
    assert any(e.id == bctx.live_event.id for e in bctx.buckets["default"])


# -- AC-10 ------------------------------------------------------------------


@given("a session with several tape entries")
def _session_several_entries(bctx: _Ctx) -> None:
    _session_with_entries(bctx)


@when("a connection attaches with since entry zero")
def _attach_since_zero(bctx: _Ctx) -> None:
    assert bctx.sctx is not None
    bucket: list[Event] = []
    bctx.buckets["reconn"] = bucket

    async def send(ev: Event) -> None:
        bucket.append(ev)
        if ev.kind == "session.replay.entry":
            await asyncio.sleep(0.01)

    # Kick attach off as a background task so the next step can race it.
    bctx.buckets["attach_task"] = []  # placeholder for parallelism marker
    bctx.extras_task = asyncio.ensure_future(  # type: ignore[attr-defined]
        bctx.sctx.attach(send, adapter_id="reconn", since_entry=0),
        loop=bctx.loop,
    )
    # Yield once so attach grabs the replay lock first.
    _run(bctx, asyncio.sleep(0))


@when("a live event is fanned out while the attach is still running")
def _live_during_replay(bctx: _Ctx) -> None:
    assert bctx.sctx is not None and bctx.session is not None
    live = new_event(
        "user.message.received",
        {"text": "LIVE"},
        session_id=bctx.session.session_id,
        source="test",
    )
    bctx.live_event = live
    _run(bctx, bctx.sctx.fanout(live))
    # Drain the attach task so all observed events land.
    _run(bctx, bctx.extras_task)  # type: ignore[attr-defined]


@then("the connection observes every replay entry before the live event")
def _replay_before_live(bctx: _Ctx) -> None:
    assert bctx.live_event is not None
    observed = bctx.buckets["reconn"]
    kinds = [e.kind for e in observed]
    done_index = kinds.index("session.replay.done")
    live_indices = [i for i, e in enumerate(observed) if e.id == bctx.live_event.id]
    assert live_indices
    assert min(live_indices) > done_index
