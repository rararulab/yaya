"""Tests for the session auto-persister — bus event → tape entry mapping."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from yaya.kernel import (
    EventBus,
    MemoryTapeStore,
    SessionStore,
    install_session_persister,
    new_event,
)

_PERSISTED_KINDS = [
    "user.message.received",
    "assistant.message.done",
    "tool.call.request",
    "tool.call.result",
    "assistant.message.delta",  # skipped but the persister still subscribes.
    "memory.query",
]


async def _setup(
    tmp_path: Path,
    session_id: str = "default",
) -> tuple[EventBus, SessionStore, Any]:
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    persister = await install_session_persister(
        bus=bus,
        store=store,
        workspace=tmp_path,
        kinds=_PERSISTED_KINDS,
    )
    # Pre-open the session so the bootstrap anchor does not race the first
    # persisted event and the test assertions count only "post-bootstrap" rows.
    await store.open(tmp_path, session_id)
    return bus, store, persister


async def test_user_and_assistant_events_round_trip(tmp_path: Path) -> None:
    """AC-02 — user.message.received + assistant.message.done both persist."""
    bus, store, persister = await _setup(tmp_path)
    try:
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "hi"},
                session_id="default",
                source="adapter-test",
            )
        )
        await bus.publish(
            new_event(
                "assistant.message.done",
                {"content": "hello", "tool_calls": []},
                session_id="default",
                source="kernel",
            )
        )
        # Drain the bus.
        await bus.close()
        session = await store.open(tmp_path, "default")
        entries = await session.entries()
        messages = [e for e in entries if e.kind == "message"]
        assert len(messages) == 2
        assert messages[0].payload == {"role": "user", "content": "hi"}
        assert messages[0].meta.get("source") == "adapter-test"
        assert messages[1].payload == {"role": "assistant", "content": "hello"}
        assert messages[1].meta.get("source") == "kernel"
    finally:
        await persister.stop()
        await store.close()


async def test_assistant_delta_is_not_persisted(tmp_path: Path) -> None:
    """AC-03 — assistant.message.delta is ignored by the persister."""
    bus, store, persister = await _setup(tmp_path)
    try:
        session = await store.open(tmp_path, "default")
        before = len(await session.entries())
        for i in range(10):
            await bus.publish(
                new_event(
                    "assistant.message.delta",
                    {"content": f"chunk-{i}"},
                    session_id="default",
                    source="llm-test",
                )
            )
        await bus.close()
        after = len(await session.entries())
        assert after == before
    finally:
        await persister.stop()
        await store.close()


async def test_persist_false_opts_out(tmp_path: Path) -> None:
    """AC-04 — ``persist=False`` in payload suppresses the entry."""
    bus, store, persister = await _setup(tmp_path)
    try:
        session = await store.open(tmp_path, "default")
        before = len(await session.entries())
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "skip me", "persist": False},
                session_id="default",
                source="adapter-test",
            )
        )
        await bus.close()
        after = len(await session.entries())
        assert after == before
    finally:
        await persister.stop()
        await store.close()


async def test_tool_call_and_result_persist(tmp_path: Path) -> None:
    """tool.call.request and tool.call.result land as tool_call / tool_result."""
    bus, store, persister = await _setup(tmp_path)
    try:
        await bus.publish(
            new_event(
                "tool.call.request",
                {"id": "c1", "name": "echo", "args": {"text": "hi"}},
                session_id="default",
                source="kernel",
            )
        )
        await bus.publish(
            new_event(
                "tool.call.result",
                {"id": "c1", "ok": True, "value": "hi"},
                session_id="default",
                source="tool-test",
            )
        )
        await bus.close()
        session = await store.open(tmp_path, "default")
        entries = await session.entries()
        kinds = [e.kind for e in entries]
        assert "tool_call" in kinds
        assert "tool_result" in kinds
    finally:
        await persister.stop()
        await store.close()


async def test_kernel_session_events_skipped(tmp_path: Path) -> None:
    """Events on session_id='kernel' never land on any tape."""
    bus, store, persister = await _setup(tmp_path)
    try:
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "ignore"},
                session_id="kernel",
                source="adapter-test",
            )
        )
        await bus.close()
        tapes = await store.list_sessions(tmp_path)
        for info in tapes:
            # No tape should hold a user message from the kernel session.
            session = await store.open(tmp_path, info.session_id)
            msgs = [e for e in await session.entries() if e.kind == "message"]
            for m in msgs:
                assert m.payload.get("content") != "ignore"
    finally:
        await persister.stop()
        await store.close()


async def test_generic_event_falls_through_as_event_entry(tmp_path: Path) -> None:
    """Any public kind without a canonical writer lands as a generic event entry."""
    bus, store, persister = await _setup(tmp_path)
    try:
        await bus.publish(
            new_event(
                "memory.query",
                {"query": "what is x", "k": 3},
                session_id="default",
                source="kernel",
            )
        )
        await bus.close()
        session = await store.open(tmp_path, "default")
        events = [e for e in await session.entries() if e.kind == "event"]
        assert any(e.payload.get("name") == "memory.query" for e in events)
    finally:
        await persister.stop()
        await store.close()


class _RaisingStore:
    """SessionStore shim whose `.open` returns a session whose append raises."""

    def __init__(self, store: SessionStore) -> None:
        self._inner = store

    async def open(self, workspace: Path, session_id: str):
        session = await self._inner.open(workspace, session_id)

        async def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("simulated tape write failure")

        session.append_message = _raise  # type: ignore[assignment]
        session.append_tool_call = _raise  # type: ignore[assignment]
        session.append_tool_result = _raise  # type: ignore[assignment]
        session.append_event = _raise  # type: ignore[assignment]
        return session


async def test_tape_failure_emits_plugin_error(tmp_path: Path) -> None:
    """AC-09 — tape-write failure emits plugin.error, bus keeps routing."""
    bus = EventBus()
    inner = SessionStore(store=MemoryTapeStore())
    raising = _RaisingStore(inner)
    errors: list[Any] = []

    async def capture(ev: Any) -> None:
        errors.append(ev)

    err_sub = bus.subscribe("plugin.error", capture, source="test")
    persister = await install_session_persister(
        bus=bus,
        store=raising,
        workspace=tmp_path,
        kinds=["user.message.received"],
    )
    try:
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "boom"},
                session_id="default",
                source="adapter-test",
            )
        )
        # Give the kernel-session worker a chance to drain the synthetic error.
        await asyncio.sleep(0.01)
        # Publish another event and verify the bus still routes it.
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "still alive"},
                session_id="default",
                source="adapter-test",
            )
        )
        await asyncio.sleep(0.01)
        assert any(ev.payload.get("name") == "kernel-session-persister" for ev in errors)
    finally:
        err_sub.unsubscribe()
        await persister.stop()
        await inner.close()
        await bus.close()


async def test_persister_stop_is_idempotent(tmp_path: Path) -> None:
    bus, store, persister = await _setup(tmp_path)
    try:
        await persister.stop()
        await persister.stop()
    finally:
        await store.close()
        await bus.close()
