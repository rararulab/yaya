"""Tests for the sqlite memory plugin.

AC-bindings from ``specs/plugin-memory_sqlite.spec``:

* round-trip → ``test_write_then_query_roundtrip``
* missing id → ``test_write_without_id_generates_uuid``
* duplicate id → ``test_duplicate_id_logs_warning``
* empty query → ``test_empty_query_tails_recent``
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.memory_sqlite.plugin import SqliteMemory


async def _fresh_plugin(tmp_path: Path) -> tuple[SqliteMemory, EventBus, KernelContext]:
    """Fresh plugin + bus + scoped context bound to ``tmp_path``."""
    plugin = SqliteMemory()
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.memory-sqlite"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("memory.query", _handler, source=plugin.name)
    bus.subscribe("memory.write", _handler, source=plugin.name)
    return plugin, bus, ctx


async def test_write_then_query_roundtrip(tmp_path: Path) -> None:
    """Write one entry, query for it, and observe it in the emitted hits."""
    plugin, bus, _ctx = await _fresh_plugin(tmp_path)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("memory.result", _observer, source="observer")

    await bus.publish(
        new_event(
            "memory.write",
            {"entry": {"id": "e-1", "text": "hello world"}},
            session_id="sess-rt-1",
            source="kernel",
        )
    )
    query_req = new_event(
        "memory.query",
        {"query": "hello", "k": 5},
        session_id="sess-rt-1",
        source="kernel",
    )
    await bus.publish(query_req)

    assert len(captured) == 1
    result = captured[0].payload
    assert result["request_id"] == query_req.id
    hits = result["hits"]
    assert len(hits) == 1
    assert hits[0]["id"] == "e-1"
    assert hits[0]["text"] == "hello world"

    await plugin.on_unload(_ctx)


async def test_write_without_id_generates_uuid(tmp_path: Path) -> None:
    """Entry without id gets a uuid4 hex that appears in the next query."""
    plugin, bus, _ctx = await _fresh_plugin(tmp_path)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("memory.result", _observer, source="observer")

    await bus.publish(
        new_event(
            "memory.write",
            {"entry": {"text": "anon"}},
            session_id="sess-uuid",
            source="kernel",
        )
    )
    await bus.publish(
        new_event(
            "memory.query",
            {"query": "anon", "k": 5},
            session_id="sess-uuid",
            source="kernel",
        )
    )

    assert len(captured) == 1
    hits = captured[0].payload["hits"]
    assert len(hits) == 1
    # uuid4 hex is 32 lowercase hex chars.
    assert len(hits[0]["id"]) == 32
    assert all(c in "0123456789abcdef" for c in hits[0]["id"])

    await plugin.on_unload(_ctx)


async def test_duplicate_id_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Re-writing the same id logs a WARNING and does not raise."""
    plugin, bus, _ctx = await _fresh_plugin(tmp_path)

    await bus.publish(
        new_event(
            "memory.write",
            {"entry": {"id": "dup", "text": "first"}},
            session_id="sess-dup",
            source="kernel",
        )
    )

    caplog.set_level(logging.WARNING, logger="plugin.memory-sqlite")
    await bus.publish(
        new_event(
            "memory.write",
            {"entry": {"id": "dup", "text": "second"}},
            session_id="sess-dup",
            source="kernel",
        )
    )

    # A WARNING mentioning the id must be present; no exception escaped the bus.
    assert any("duplicate id" in rec.getMessage() and "dup" in rec.getMessage() for rec in caplog.records)

    await plugin.on_unload(_ctx)


async def test_empty_query_tails_recent(tmp_path: Path) -> None:
    """Empty query returns the last-N rows ordered by ts desc."""
    plugin, bus, _ctx = await _fresh_plugin(tmp_path)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("memory.result", _observer, source="observer")

    for i, ts in enumerate([1.0, 2.0, 3.0]):
        await bus.publish(
            new_event(
                "memory.write",
                {"entry": {"id": f"e-{i}", "text": f"row {i}", "ts": ts}},
                session_id="sess-tail",
                source="kernel",
            )
        )

    await bus.publish(
        new_event(
            "memory.query",
            {"query": "", "k": 2},
            session_id="sess-tail",
            source="kernel",
        )
    )

    assert len(captured) == 1
    hits = captured[0].payload["hits"]
    assert [h["id"] for h in hits] == ["e-2", "e-1"]

    await plugin.on_unload(_ctx)
