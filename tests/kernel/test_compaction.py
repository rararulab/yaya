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


# ---------------------------------------------------------------------------
# #93 P2 — retry / backoff / disable coverage.
# ---------------------------------------------------------------------------


async def _drain(bus: EventBus) -> None:
    """Yield enough to let the bus deliver pending events to subscribers."""
    for _ in range(20):
        await asyncio.sleep(0)


async def test_manager_retries_three_times_then_disables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summariser raises every attempt → 3 attempts, single terminal failed event."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    # Kill the retry backoff so the test runs fast.
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s=0: _real_sleep(0))

    failed_events: list[Any] = []

    async def _sink(ev: Any) -> None:
        failed_events.append(ev)

    attempts = {"n": 0}

    class _AlwaysFailing:
        async def summarize(self, entries: list[Any], target_tokens: int) -> str:
            attempts["n"] += 1
            raise RuntimeError("nope")

    try:
        bus.subscribe("session.compaction.failed", _sink, source="test")
        session = await store.open(tmp_path, "retries")
        for _ in range(6):
            await session.append_message("user", "x" * 50)

        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=_AlwaysFailing(),
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "go"},
                session_id="retries",
                source="test",
            ),
        )
        # Wait for the retry loop to exhaust.
        for _ in range(50):
            if attempts["n"] >= 3:
                break
            await asyncio.sleep(0)
        # Let the terminal .failed event propagate to the subscriber.
        await bus.close()

        assert attempts["n"] == 3, "expected exactly 3 summariser attempts"
        assert len(failed_events) == 1, f"expected a single terminal failed event, got {len(failed_events)}"
        payload = failed_events[0].payload
        assert payload["attempts"] == 3
        assert payload["target_session_id"] == "retries"
        assert "nope" in payload["error"]
        assert "retries" in mgr._disabled
        assert "retries" not in mgr._attempts
        await mgr.stop()
    finally:
        await store.close()


async def test_manager_skips_disabled_session_on_next_trigger(
    tmp_path: Path,
) -> None:
    """A session present in _disabled short-circuits: no summariser call, no events."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    summ = _FakeSummarizer()
    seen: list[Any] = []

    async def _sink(ev: Any) -> None:
        seen.append(ev)

    try:
        bus.subscribe("session.compaction.started", _sink, source="test")
        bus.subscribe("session.compaction.completed", _sink, source="test")
        bus.subscribe("session.compaction.failed", _sink, source="test")

        session = await store.open(tmp_path, "dead")
        for _ in range(5):
            await session.append_message("user", "z" * 60)

        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=summ,
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )
        # Pre-populate the disabled set so the guard short-circuits.
        mgr._mark_disabled("dead")

        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "trigger"},
                session_id="dead",
                source="test",
            ),
        )
        await _drain(bus)
        await mgr.stop()
        await bus.close()

        assert summ.calls == [], "disabled session must not invoke the summariser"
        assert seen == [], "disabled session must not emit compaction events"
    finally:
        await store.close()


async def test_disabled_set_evicts_oldest_at_cap(tmp_path: Path) -> None:
    """_disabled is bounded by _INFLIGHT_CAP via FIFO eviction (lesson #6)."""
    from yaya.kernel.compaction import _INFLIGHT_CAP

    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    try:
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=_FakeSummarizer(),
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=1_000_000,
            target_tokens=100,
        )
        # Fill the disabled map to capacity.
        for i in range(_INFLIGHT_CAP):
            mgr._mark_disabled(f"s-{i}")
        assert len(mgr._disabled) == _INFLIGHT_CAP
        assert "s-0" in mgr._disabled
        # One over the cap evicts the oldest entry (FIFO).
        mgr._mark_disabled("s-overflow")
        assert len(mgr._disabled) == _INFLIGHT_CAP
        assert "s-0" not in mgr._disabled
        assert "s-overflow" in mgr._disabled
        await mgr.stop()
    finally:
        await bus.close()
        await store.close()


async def test_mark_disabled_purges_attempts_row(tmp_path: Path) -> None:
    """Adding a session to _disabled drops its _attempts counter (lesson #6)."""
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    try:
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=_FakeSummarizer(),
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=1_000_000,
            target_tokens=100,
        )
        mgr._attempts["live"] = 2
        mgr._mark_disabled("live")
        assert "live" not in mgr._attempts
        assert "live" in mgr._disabled
        await mgr.stop()
    finally:
        await bus.close()
        await store.close()


async def test_manager_emits_single_failed_after_retry_exhaustion_with_attempts_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: three attempts → exactly one terminal .failed(attempts=3)."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s=0: _real_sleep(0))
    bus = EventBus()
    store = SessionStore(store=MemoryTapeStore())
    failed: list[Any] = []

    async def _sink(ev: Any) -> None:
        failed.append(ev)

    try:
        bus.subscribe("session.compaction.failed", _sink, source="test")
        session = await store.open(tmp_path, "triple")
        for _ in range(6):
            await session.append_message("user", "x" * 50)
        mgr = await install_compaction_manager(
            bus=bus,
            store=store,
            summarizer=_ExplodingSummarizer(),
            workspace=tmp_path,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "boom"},
                session_id="triple",
                source="test",
            ),
        )
        # Give the retry loop enough scheduling turns.
        for _ in range(100):
            if failed:
                break
            await asyncio.sleep(0)
        await bus.close()
        await mgr.stop()
        assert len(failed) == 1
        assert failed[0].payload.get("attempts") == 3
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# #93 P3 — after_last_anchor regression test.
# ---------------------------------------------------------------------------


async def test_after_last_anchor_filters_handoff_events(tmp_path: Path) -> None:
    """Synthetic handoff events republic emits after an anchor are stripped.

    Without this filter, auto-compaction would re-summarise its own
    marker on every pass (#29 root cause). The fix lives in
    :func:`yaya.kernel.tape_context.after_last_anchor`; this test
    pins it.
    """
    from yaya.kernel import after_last_anchor

    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "anchor-filter")
        # Append a compaction-style anchor and then a user message.
        await session.handoff("compaction", state={"kind": "compaction", "summary": "s"})
        await session.append_message("user", "after")
        entries = await after_last_anchor(session.manager, session.tape_name)
        # The republic handoff(kind=event, name=handoff) must be filtered
        # out; only the user message survives.
        kinds = [(e.kind, e.payload.get("name")) for e in entries]
        assert ("event", "handoff") not in kinds
        user_messages = [e for e in entries if e.kind == "message"]
        assert len(user_messages) == 1
        assert user_messages[0].payload.get("content") == "after"
    finally:
        await store.close()
