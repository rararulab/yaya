"""Tests for :mod:`yaya.kernel.compaction` — Summarizer, manager, estimator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel import (
    COMPACTION_ANCHOR_KIND,
    EventBus,
    MemoryTapeStore,
    Session,
    SessionStore,
    compact_session,
    default_tape_context,
    estimate_text_tokens,
    install_compaction_manager,
    new_event,
    select_messages,
    should_auto_compact,
)


@dataclass
class _FakeSummarizer:
    """Deterministic summariser: echoes the entry count + a fixed tag."""

    tag: str = "SUMMARY"
    calls: list[int] = field(default_factory=list)

    async def summarize(self, entries: list[Any], target_tokens: int) -> str:
        self.calls.append(len(entries))
        return f"{self.tag}:{len(entries)}:{target_tokens}"


class _ExplodingSummarizer:
    """Always-raising summariser for failure-path tests."""

    async def summarize(self, entries: list[Any], target_tokens: int) -> str:
        raise RuntimeError("summariser down")


# ---------------------------------------------------------------------------
# Heuristics.
# ---------------------------------------------------------------------------


async def test_estimator_is_deterministic(tmp_path: Path) -> None:
    """AC-03 — estimate_text_tokens returns the same value across calls."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "est")
        for i in range(3):
            await session.append_message("user", f"hello-{i}")
        entries = await session.entries()
        first = estimate_text_tokens(entries)
        second = estimate_text_tokens(entries)
        assert first == second
        assert first > 0
    finally:
        await store.close()


def test_estimator_empty_tape() -> None:
    """Edge case: estimator returns 0 for an empty entry list."""
    assert estimate_text_tokens([]) == 0


def test_should_auto_compact_threshold_zero_disables() -> None:
    """threshold<=0 disables compaction even for large contexts."""
    assert should_auto_compact(1_000_000, threshold=0) is False
    assert should_auto_compact(1_000_000, threshold=-1) is False


def test_should_auto_compact_boundary() -> None:
    """At-threshold triggers; below-threshold does not."""
    assert should_auto_compact(100, threshold=100) is True
    assert should_auto_compact(99, threshold=100) is False


# ---------------------------------------------------------------------------
# Manual compact.
# ---------------------------------------------------------------------------


async def test_manual_compact_appends_anchor(tmp_path: Path) -> None:
    """Session.compact appends a compaction anchor and returns the summary."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "manual")
        for i in range(5):
            await session.append_message("user", f"msg-{i}")
        summ = _FakeSummarizer()
        summary = await session.compact(summ)
        assert summary.startswith("SUMMARY:")
        assert summ.calls == [5]
        entries = await session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        # session/start + compaction
        assert len(anchors) == 2
        compaction_anchor = anchors[-1]
        state = compaction_anchor.payload.get("state")
        assert isinstance(state, dict)
        assert state.get("kind") == COMPACTION_ANCHOR_KIND
        assert state.get("summary") == summary
        assert isinstance(state.get("tokens_before"), int)
    finally:
        await store.close()


async def test_compact_empty_postanchor_is_noop(tmp_path: Path) -> None:
    """No post-anchor entries → no compaction anchor, empty return."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "empty")
        summ = _FakeSummarizer()
        result = await session.compact(summ)
        assert result == ""
        entries = await session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        # Only the bootstrap anchor.
        assert len(anchors) == 1
        assert anchors[0].payload.get("name") == "session/start"
    finally:
        await store.close()


async def test_post_compaction_context_injects_summary(tmp_path: Path) -> None:
    """select_messages injects the summary as role=system when crossing a compaction anchor."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "ctx")
        await session.append_message("user", "old-1")
        await session.append_message("assistant", "old-2")
        summ = _FakeSummarizer(tag="GIST")
        await session.compact(summ)
        # Default context reads the full tape and renders the summary
        # system message; no pre-anchor user/assistant messages are
        # present because immediately after compact() the post-anchor
        # window is empty (they've all been elided behind the summary).
        messages = await session.context()
        system_messages = [m for m in messages if m.get("role") == "system"]
        assert len(system_messages) == 1
        assert "GIST:2" in system_messages[0]["content"]
        # No user / assistant messages survive after the anchor because
        # the tape carries nothing post-anchor yet.
        assert all(m.get("role") == "system" for m in messages)
    finally:
        await store.close()


async def test_fork_compact_does_not_mutate_parent(tmp_path: Path) -> None:
    """Compacting on a child fork leaves the parent tape unchanged."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        parent = await store.open(tmp_path, "parent")
        for i in range(3):
            await parent.append_message("user", f"p-{i}")
        parent_count_before = len(await parent.entries())
        child = parent.fork("child")
        await child.compact(_FakeSummarizer())
        # Parent tape must be byte-identical in entry count; the child's
        # compaction anchor lives only in the overlay.
        assert len(await parent.entries()) == parent_count_before
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Events.
# ---------------------------------------------------------------------------


async def test_compact_emits_started_and_completed(tmp_path: Path) -> None:
    """compact_session publishes started + completed on session_id=kernel."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    seen: list[Any] = []

    async def _sink(ev: Any) -> None:
        seen.append(ev)

    try:
        bus.subscribe("session.compaction.started", _sink, source="test")
        bus.subscribe("session.compaction.completed", _sink, source="test")
        bus.subscribe("session.compaction.failed", _sink, source="test")
        session = await store.open(tmp_path, "evts")
        await session.append_message("user", "abcdef")
        await compact_session(session, _FakeSummarizer(), bus=bus)
        # Drain the kernel worker so handlers actually run.
        await bus.close()
        kinds = [ev.kind for ev in seen]
        assert "session.compaction.started" in kinds
        assert "session.compaction.completed" in kinds
        assert "session.compaction.failed" not in kinds
        for ev in seen:
            assert ev.session_id == "kernel"
            assert ev.payload.get("target_session_id") == "evts"
    finally:
        await store.close()


async def test_compact_failure_emits_failed_and_does_not_anchor(tmp_path: Path) -> None:
    """Summariser raises → failed event + tape unchanged (lesson #29)."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    seen: list[Any] = []

    async def _sink(ev: Any) -> None:
        seen.append(ev)

    try:
        bus.subscribe("session.compaction.failed", _sink, source="test")
        session = await store.open(tmp_path, "boom")
        await session.append_message("user", "please-fail")
        before = await session.entries()
        with pytest.raises(RuntimeError):
            await compact_session(session, _ExplodingSummarizer(), bus=bus)
        after = await session.entries()
        assert len(after) == len(before)  # No new anchor written.
        await bus.close()
        kinds = [ev.kind for ev in seen]
        assert "session.compaction.failed" in kinds
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Auto-trigger manager.
# ---------------------------------------------------------------------------


async def test_manager_does_nothing_below_threshold(tmp_path: Path) -> None:
    """No compaction when the post-anchor window is below threshold."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    summ = _FakeSummarizer()
    try:
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=summ,
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=1_000_000,
            target_tokens=10_000,
        )
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "short"},
                session_id="low",
                source="test",
            ),
        )
        await asyncio.sleep(0.05)
        await mgr.stop()
        assert summ.calls == []
    finally:
        await bus.close()
        await store.close()


async def test_manager_auto_triggers_once_past_threshold(tmp_path: Path) -> None:
    """Threshold breach schedules compaction; summariser is invoked."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    summ = _FakeSummarizer()
    try:
        # Seed a session with enough entries to cross the low threshold.
        session = await store.open(tmp_path, "auto")
        for _i in range(5):
            await session.append_message("user", "x" * 40)
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=summ,
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "trigger"},
                session_id="auto",
                source="test",
            ),
        )
        # Drain via close; that also awaits outstanding workers.
        await asyncio.sleep(0.1)
        # Give the spawned task a couple of scheduling rounds.
        for _ in range(5):
            if summ.calls:
                break
            await asyncio.sleep(0.05)
        await mgr.stop()
        assert summ.calls, "expected the auto-manager to call the summariser"
    finally:
        await bus.close()
        await store.close()


async def test_manager_single_inflight_guard(tmp_path: Path) -> None:
    """Rapid successive triggers only produce one concurrent compaction."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())

    slow_gate = asyncio.Event()

    class _SlowSummarizer:
        def __init__(self) -> None:
            self.calls = 0

        async def summarize(self, entries: list[Any], target_tokens: int) -> str:
            self.calls += 1
            await slow_gate.wait()
            return "slow"

    summ = _SlowSummarizer()
    try:
        session = await store.open(tmp_path, "race")
        for _ in range(4):
            await session.append_message("user", "y" * 50)
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=summ,
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )
        for i in range(3):
            await bus.publish(
                new_event(
                    "user.message.received",
                    {"text": f"trig-{i}"},
                    session_id="race",
                    source="test",
                ),
            )
        await asyncio.sleep(0.15)
        # Only one in-flight compaction should have started.
        assert summ.calls == 1
        slow_gate.set()
        await asyncio.sleep(0.05)
        await mgr.stop()
    finally:
        await bus.close()
        await store.close()


async def test_manager_ignores_kernel_session(tmp_path: Path) -> None:
    """Control-plane events on session_id=kernel are never compacted."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    summ = _FakeSummarizer()
    try:
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=summ,
            workspace=tmp_path,
            kinds=["kernel.ready"],
            threshold_tokens=1,
            target_tokens=10,
        )
        await bus.publish(
            new_event(
                "kernel.ready",
                {"version": "test"},
                session_id="kernel",
                source="kernel",
            ),
        )
        await asyncio.sleep(0.05)
        await mgr.stop()
        assert summ.calls == []
    finally:
        await bus.close()
        await store.close()


async def test_default_context_skips_non_compaction_anchors(tmp_path: Path) -> None:
    """select_messages still skips plain handoff anchors (only compaction injects)."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "plain")
        await session.handoff("manual", state={"kind": "other", "note": "x"})
        await session.append_message("user", "after")
        entries = await session.entries()
        ctx = default_tape_context()
        messages = select_messages(entries, ctx)
        # No system message from the non-compaction anchor.
        assert all(m.get("role") != "system" for m in messages)
        assert any(m.get("role") == "user" for m in messages)
    finally:
        await store.close()


async def test_session_compact_helper_returns_string(tmp_path: Path) -> None:
    """Session.compact delegates to compact_session (smoke test)."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session: Session = await store.open(tmp_path, "delegate")
        await session.append_message("user", "delegate-me")
        out = await session.compact(_FakeSummarizer())
        assert isinstance(out, str)
        assert out
    finally:
        await store.close()
