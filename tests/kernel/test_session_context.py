"""Tests for SessionContext / SessionManager — multi-connection fanout (#36)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yaya.kernel import (
    ConnectionLimitError,
    Event,
    EventBus,
    MemoryTapeStore,
    SessionContext,
    SessionManager,
    SessionStore,
    install_session_manager,
    new_event,
)


async def _open_session(tmp_path: Path, session_id: str = "s") -> tuple[SessionStore, Path, object]:
    """Return (store, workspace, session) with a fresh memory tape."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    store = SessionStore(store=MemoryTapeStore())
    session = await store.open(workspace, session_id)
    return store, workspace, session


async def test_fanout_reaches_every_connection(tmp_path: Path) -> None:
    """AC-01 — one event fans out to every attached connection exactly once."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                a_events: list[Event] = []
                b_events: list[Event] = []

                async def send_a(ev: Event) -> None:
                    a_events.append(ev)

                async def send_b(ev: Event) -> None:
                    b_events.append(ev)

                await ctx.attach(send_a, adapter_id="web-a")
                await ctx.attach(send_b, adapter_id="web-b")
                ev = new_event(
                    "user.message.received",
                    {"text": "hi"},
                    session_id=session.session_id,
                    source="test",
                )
                await ctx.fanout(ev)
                assert [e.id for e in a_events] == [ev.id]
                assert [e.id for e in b_events] == [ev.id]
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_event_ordering_is_identical_across_connections(tmp_path: Path) -> None:
    """AC-02 — 100 events observed in identical order on two connections."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                a_events: list[str] = []
                b_events: list[str] = []

                async def send_a(ev: Event) -> None:
                    a_events.append(ev.id)

                async def send_b(ev: Event) -> None:
                    b_events.append(ev.id)

                await ctx.attach(send_a, adapter_id="a")
                await ctx.attach(send_b, adapter_id="b")
                for i in range(100):
                    ev = new_event(
                        "user.message.received",
                        {"text": f"m-{i}"},
                        session_id=session.session_id,
                        source="test",
                    )
                    await ctx.fanout(ev)
                assert a_events == b_events
                assert len(a_events) == 100
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_reconnect_replay_emits_missed_entries(tmp_path: Path) -> None:
    """AC-03 — since_entry replays entries with id > since_entry, then replay.done."""
    store, _, session = await _open_session(tmp_path)
    try:
        # Seed tape with five user messages (bootstrap anchor already exists).
        for i in range(5):
            await session.append_message("user", f"m-{i}")
        entries = await session.entries()
        mid_id = entries[len(entries) // 2].id

        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                captured: list[Event] = []

                async def send(ev: Event) -> None:
                    captured.append(ev)

                await ctx.attach(send, adapter_id="resume", since_entry=mid_id)
                kinds = [e.kind for e in captured]
                assert kinds.count("session.replay.done") == 1
                replay_entries = [e for e in captured if e.kind == "session.replay.entry"]
                # Every replayed entry has id strictly greater than mid_id.
                for e in replay_entries:
                    assert e.payload["entry_id"] > mid_id
                # The final event is the done sentinel.
                assert captured[-1].kind == "session.replay.done"
                assert captured[-1].payload["replayed"] == len(replay_entries)
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_connection_cap_is_enforced(tmp_path: Path) -> None:
    """AC-04 — exceeding max_connections raises ConnectionLimitError."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus, max_connections=2)
            try:

                async def send(_: Event) -> None:
                    return None

                await ctx.attach(send)
                await ctx.attach(send)
                with pytest.raises(ConnectionLimitError, match="2-connection cap"):
                    await ctx.attach(send)
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_heartbeat_reap_detaches_stale_connection(tmp_path: Path) -> None:
    """AC-05 — reap sweep drops a connection whose last_seen is too old."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            # Deterministic clock via the ``now`` hook.
            clock = {"t": 1000.0}

            def now() -> float:
                return clock["t"]

            ctx = SessionContext(
                session=session,
                bus=bus,
                heartbeat_timeout_s=30.0,
                now=now,
            )
            try:
                sent: list[Event] = []

                async def send(ev: Event) -> None:
                    sent.append(ev)

                captured: list[Event] = []

                async def observer(ev: Event) -> None:
                    captured.append(ev)

                bus.subscribe("session.context.detached", observer, source="test")

                conn = await ctx.attach(send, adapter_id="stale")
                # Advance the clock beyond the heartbeat window.
                clock["t"] = 2000.0
                await ctx._reap_once()

                assert conn.id not in {c.id for c in ctx.connections()}
                # Let the bus flush the detached event.
                await asyncio.sleep(0)
                assert any(
                    ev.payload.get("connection_id") == conn.id and ev.payload.get("reason") == "timeout"
                    for ev in captured
                )
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_lifecycle_events_route_on_kernel_session(tmp_path: Path) -> None:
    """AC-06 — session.context.* envelopes ride session_id="kernel"."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            captured: list[Event] = []

            async def observer(ev: Event) -> None:
                captured.append(ev)

            bus.subscribe("session.context.attached", observer, source="test")

            ctx = SessionContext(session=session, bus=bus)
            try:

                async def send(_: Event) -> None:
                    return None

                await ctx.attach(send, adapter_id="web")
                await asyncio.sleep(0)  # flush kernel-session drain
                assert any(ev.session_id == "kernel" for ev in captured)
                assert any(ev.payload.get("session_id") == session.session_id for ev in captured)
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_send_failure_detaches_and_preserves_fanout(tmp_path: Path) -> None:
    """AC-07 — raising send detaches offender; healthy connection still receives."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                good: list[Event] = []
                detached_events: list[Event] = []

                async def send_good(ev: Event) -> None:
                    good.append(ev)

                async def send_bad(_: Event) -> None:
                    raise RuntimeError("transport closed")

                async def observer(ev: Event) -> None:
                    detached_events.append(ev)

                bus.subscribe("session.context.detached", observer, source="test")

                good_conn = await ctx.attach(send_good, adapter_id="good")
                bad_conn = await ctx.attach(send_bad, adapter_id="bad")
                assert good_conn.id != bad_conn.id  # attach assigns distinct ids

                ev = new_event(
                    "user.message.received",
                    {"text": "hi"},
                    session_id=session.session_id,
                    source="test",
                )
                await ctx.fanout(ev)
                await asyncio.sleep(0)

                assert [e.id for e in good] == [ev.id]
                assert bad_conn.id not in {c.id for c in ctx.connections()}
                assert any(
                    e.payload.get("connection_id") == bad_conn.id and e.payload.get("reason") == "send_failed"
                    for e in detached_events
                )
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_close_detaches_with_shutdown_reason(tmp_path: Path) -> None:
    """AC-08 — close emits detached(reason="shutdown") for every conn."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            captured: list[Event] = []

            async def observer(ev: Event) -> None:
                captured.append(ev)

            bus.subscribe("session.context.detached", observer, source="test")

            ctx = SessionContext(session=session, bus=bus)

            async def send(_: Event) -> None:
                return None

            c1 = await ctx.attach(send, adapter_id="a")
            c2 = await ctx.attach(send, adapter_id="b")
            await ctx.close()
            await asyncio.sleep(0)

            shutdown_ids = {
                ev.payload.get("connection_id") for ev in captured if ev.payload.get("reason") == "shutdown"
            }
            assert c1.id in shutdown_ids
            assert c2.id in shutdown_ids
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_manager_routes_bus_events_to_matching_context(tmp_path: Path) -> None:
    """AC-09 — a bus event on session S reaches every conn attached to S."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = SessionStore(store=MemoryTapeStore())
    try:
        bus = EventBus()
        try:
            manager: SessionManager = await install_session_manager(
                bus=bus,
                store=store,
                workspace=workspace,
                kinds=["user.message.received"],
            )
            try:
                received: list[Event] = []

                async def send(ev: Event) -> None:
                    received.append(ev)

                await manager.attach("default", send, adapter_id="web")
                ev = new_event(
                    "user.message.received",
                    {"text": "hi"},
                    session_id="default",
                    source="test",
                )
                await bus.publish(ev)
                assert any(e.id == ev.id for e in received)

                snap = manager.snapshot()
                assert snap and snap[0]["session_id"] == "default"
                assert snap[0]["connection_count"] == 1
            finally:
                await manager.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_live_event_buffers_behind_replay(tmp_path: Path) -> None:
    """AC-10 — a live fanout during replay sits behind the replay lock."""
    store, _, session = await _open_session(tmp_path)
    try:
        for i in range(3):
            await session.append_message("user", f"m-{i}")

        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                observed: list[Event] = []

                async def send(ev: Event) -> None:
                    observed.append(ev)
                    # Slow down replay writes slightly so a concurrent
                    # fanout has time to race the lock.
                    if ev.kind == "session.replay.entry":
                        await asyncio.sleep(0.01)

                # Fire attach + a concurrent live fanout.
                live = new_event(
                    "user.message.received",
                    {"text": "LIVE"},
                    session_id=session.session_id,
                    source="test",
                )
                attach_task = asyncio.create_task(
                    ctx.attach(send, adapter_id="reconn", since_entry=0),
                )
                # Yield so attach grabs the lock first.
                await asyncio.sleep(0)
                await ctx.fanout(live)
                await attach_task

                kinds = [e.kind for e in observed]
                done_index = kinds.index("session.replay.done")
                live_indices = [i for i, e in enumerate(observed) if e.id == live.id]
                assert live_indices, "live event never observed"
                # Every live occurrence must land AFTER replay.done.
                assert min(live_indices) > done_index
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_detach_is_idempotent(tmp_path: Path) -> None:
    """Unknown / double detaches are silent — idempotency guarantee."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                await ctx.detach("nonexistent")

                async def send(_: Event) -> None:
                    return None

                conn = await ctx.attach(send)
                await ctx.detach(conn.id)
                # Second detach is a no-op (no raise).
                await ctx.detach(conn.id)
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_heartbeat_returns_false_for_unknown(tmp_path: Path) -> None:
    """Heartbeat for an unknown id returns False without mutating state."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            try:
                assert await ctx.heartbeat("bogus") is False

                async def send(_: Event) -> None:
                    return None

                conn = await ctx.attach(send)
                assert await ctx.heartbeat(conn.id) is True
            finally:
                await ctx.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_attach_on_closed_context_raises(tmp_path: Path) -> None:
    """RuntimeError guards against attach-after-close."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)
            await ctx.close()

            async def send(_: Event) -> None:
                return None

            with pytest.raises(RuntimeError, match="closed"):
                await ctx.attach(send)
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_fanout_on_closed_context_is_noop(tmp_path: Path) -> None:
    """Post-close fanout must not raise even if a send would fail."""
    store, _, session = await _open_session(tmp_path)
    try:
        bus = EventBus()
        try:
            ctx = SessionContext(session=session, bus=bus)

            async def send(_: Event) -> None:
                raise RuntimeError("should not be called")

            await ctx.attach(send)
            await ctx.close()
            ev = new_event(
                "user.message.received",
                {"text": "no-op"},
                session_id=session.session_id,
                source="test",
            )
            await ctx.fanout(ev)  # no raise
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_manager_ignores_kernel_session_events(tmp_path: Path) -> None:
    """Bus events with session_id="kernel" never fan out to user connections."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = SessionStore(store=MemoryTapeStore())
    try:
        bus = EventBus()
        try:
            manager: SessionManager = await install_session_manager(
                bus=bus,
                store=store,
                workspace=workspace,
                kinds=["plugin.loaded"],
            )
            try:
                seen: list[Event] = []

                async def send(ev: Event) -> None:
                    seen.append(ev)

                await manager.attach("default", send, adapter_id="web")
                ev = new_event(
                    "plugin.loaded",
                    {"name": "x", "version": "1", "category": "tool"},
                    session_id="kernel",
                    source="kernel",
                )
                await bus.publish(ev)
                assert not any(e.id == ev.id for e in seen)
            finally:
                await manager.close()
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_manager_detach_and_heartbeat_forward(tmp_path: Path) -> None:
    """Manager.detach / manager.heartbeat forward to the owning context."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = SessionStore(store=MemoryTapeStore())
    try:
        bus = EventBus()
        try:
            manager = SessionManager(bus=bus, store=store, workspace=workspace)
            try:

                async def send(_: Event) -> None:
                    return None

                # Unknown session forwards gracefully.
                assert await manager.heartbeat("ghost", "x") is False
                await manager.detach("ghost", "x")  # silent

                conn = await manager.attach("default", send)
                assert await manager.heartbeat("default", conn.id) is True
                await manager.detach("default", conn.id)
                ctx = manager.get("default")
                assert ctx is not None
                assert conn.id not in {c.id for c in ctx.connections()}
            finally:
                await manager.close()
        finally:
            await bus.close()
    finally:
        await store.close()
