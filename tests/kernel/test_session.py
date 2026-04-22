"""Tests for :mod:`yaya.kernel.session` — SessionStore, Session, tape ops."""

from __future__ import annotations

from pathlib import Path

import pytest

from yaya.kernel import (
    EventBus,
    KernelContext,
    MemoryTapeStore,
    SessionInfo,
    SessionStore,
    after_last_anchor,
    default_session_dir,
    tape_name_for,
)


async def _open(
    store: SessionStore,
    workspace: Path,
    session_id: str = "default",
):
    return await store.open(workspace, session_id)


async def test_open_seeds_session_start_anchor(tmp_path: Path) -> None:
    """AC-01 — opening a fresh session seeds a session/start anchor."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path)
        entries = await session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        assert len(anchors) == 1
        assert anchors[0].payload.get("name") == "session/start"
        state = anchors[0].payload.get("state")
        assert isinstance(state, dict)
        assert state.get("owner") == "human"
        assert state.get("workspace") == str(tmp_path)
    finally:
        await store.close()


async def test_reset_archives_then_clears(tmp_path: Path) -> None:
    """AC-05 — reset(archive=True) dumps jsonl + clears + reseeds anchor."""
    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await _open(store, tmp_path, "reset-me")
        for i in range(10):
            await session.append_message("user", f"msg-{i}")
        archive_path = await session.reset(archive=True)
        assert archive_path is not None
        assert archive_path.exists()
        assert archive_path.parent == tapes_dir / ".archive"
        entries = await session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        assert len(anchors) == 1
        assert anchors[0].payload.get("name") == "session/start"
    finally:
        await store.close()


async def test_workspace_scoped_tape_names(tmp_path: Path) -> None:
    """AC-06 — different workspaces with the same id get different tape names."""
    ws1 = tmp_path / "w1"
    ws2 = tmp_path / "w2"
    ws1.mkdir()
    ws2.mkdir()
    store = SessionStore(store=MemoryTapeStore())
    try:
        s1 = await _open(store, ws1, "default")
        s2 = await _open(store, ws2, "default")
        assert s1.tape_name != s2.tape_name
        await s1.append_message("user", "w1-only")
        rows_ws1 = await store.list_sessions(ws1)
        rows_ws2 = await store.list_sessions(ws2)
        assert len(rows_ws1) == 1
        assert len(rows_ws2) == 1
        assert rows_ws1[0].tape_name != rows_ws2[0].tape_name
    finally:
        await store.close()


async def test_fork_isolates_child_writes(tmp_path: Path) -> None:
    """AC-07 — fork reads parent + own; writes never land on parent."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        parent = await _open(store, tmp_path, "parent")
        for i in range(4):
            await parent.append_message("user", f"p-{i}")
        parent_before = await parent.info()
        parent_count_before = parent_before.entry_count

        child = parent.fork("child")
        for i in range(3):
            await child.append_message("user", f"c-{i}")

        parent_after = await parent.info()
        assert parent_after.entry_count == parent_count_before, "parent must remain untouched by child writes"
        child_info = await child.info()
        assert child_info.entry_count == parent_count_before + 3, "child sees parent + own entries"

        child_msgs = await child.context()
        # 4 parent message entries + 3 child message entries (anchor + handoff-event skipped).
        assert len(child_msgs) == 7
    finally:
        await store.close()


async def test_after_last_anchor_helper(tmp_path: Path) -> None:
    """AC-08 — after_last_anchor returns only post-compaction entries."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "compact")
        await session.append_message("user", "pre-compaction-1")
        await session.append_message("assistant", "pre-compaction-2")
        await session.handoff("compaction/0", state={"summary": "chat so far"})
        await session.append_message("user", "post-1")
        await session.append_message("assistant", "post-2")
        post = await after_last_anchor(session.manager, session.tape_name)
        messages = [e for e in post if e.kind == "message"]
        assert len(messages) == 2
        assert messages[0].payload.get("content") == "post-1"
        assert messages[1].payload.get("content") == "post-2"
    finally:
        await store.close()


async def test_kernel_context_exposes_session(tmp_path: Path) -> None:
    """AC-10 — KernelContext.session returns the bound Session, read-only."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path)
        bus = EventBus()
        try:
            ctx = KernelContext(
                bus=bus,
                logger=None,
                config={},
                state_dir=tmp_path,
                plugin_name="test-plugin",
                session=session,
            )
            assert ctx.session is session
            with pytest.raises(AttributeError):
                ctx.session = None  # type: ignore[misc]
        finally:
            await bus.close()
    finally:
        await store.close()


async def test_info_reports_entry_and_anchor_counts(tmp_path: Path) -> None:
    """`SessionInfo` tracks entries and the last anchor name."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path)
        info = await session.info()
        assert isinstance(info, SessionInfo)
        # handoff appends (anchor, event) pair so bootstrap has 2 entries.
        assert info.entry_count >= 1
        assert info.last_anchor == "session/start"
        before = info.entry_count
        await session.append_message("user", "hi")
        await session.handoff("checkpoint/1", state={})
        info2 = await session.info()
        assert info2.entry_count > before
        assert info2.last_anchor == "checkpoint/1"
    finally:
        await store.close()


async def test_append_tool_call_and_result_round_trip(tmp_path: Path) -> None:
    """`append_tool_call` / `append_tool_result` land on the tape as expected."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path)
        await session.append_tool_call({
            "id": "call-1",
            "name": "echo",
            "args": {"text": "hi"},
        })
        await session.append_tool_result(
            tool_call_id="call-1",
            result={"ok": True, "value": "hi"},
        )
        entries = await session.entries()
        kinds = [e.kind for e in entries]
        assert "tool_call" in kinds
        assert "tool_result" in kinds
    finally:
        await store.close()


async def test_file_backed_store_roundtrip(tmp_path: Path) -> None:
    """File-backed store persists + reloads across SessionStore instances."""
    tapes_dir = tmp_path / "tapes"
    store1 = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store1.open(tmp_path, "persist-test")
        await session.append_message("user", "saved")
    finally:
        await store1.close()

    store2 = SessionStore(tapes_dir=tapes_dir)
    try:
        session2 = await store2.open(tmp_path, "persist-test")
        entries = await session2.entries()
        messages = [e for e in entries if e.kind == "message"]
        assert len(messages) == 1
        assert messages[0].payload.get("content") == "saved"
    finally:
        await store2.close()


def test_default_session_dir_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`default_session_dir` respects YAYA_STATE_DIR override."""
    monkeypatch.setenv("YAYA_STATE_DIR", str(tmp_path))
    assert default_session_dir() == tmp_path / "tapes"


def test_default_session_dir_falls_back_to_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`default_session_dir` uses XDG_STATE_HOME when YAYA_STATE_DIR is unset."""
    monkeypatch.delenv("YAYA_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_session_dir() == tmp_path / "yaya" / "tapes"


def test_tape_name_for_is_deterministic(tmp_path: Path) -> None:
    """`tape_name_for` is stable for the same (workspace, session_id)."""
    a = tape_name_for(tmp_path, "default")
    b = tape_name_for(tmp_path, "default")
    assert a == b
    c = tape_name_for(tmp_path, "other")
    assert a != c


async def test_session_tail_bounded_memory(tmp_path: Path) -> None:
    """File-backed ``Session.tail`` streams via a bounded deque (PR #88 finding).

    Reproduces the ``yaya session show --tail N`` hot path: writes many
    entries to the file store, asks for the last few, and asserts only
    those come back. The deque cap is what makes this safe for 10 MB
    tapes; the test does not try to measure RSS directly — it pins the
    public contract (return order + count) so a regression to
    ``entries()[-n:]`` would surface via the full-load sanity check
    below.
    """
    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store.open(tmp_path, "tail-me")
        for i in range(50):
            await session.append_message("user", f"msg-{i}")
        last5 = await session.tail(5)
        assert len(last5) == 5
        contents = [e.payload.get("content") for e in last5 if e.kind == "message"]
        assert contents == ["msg-45", "msg-46", "msg-47", "msg-48", "msg-49"]
        # n <= 0 is a no-op.
        assert await session.tail(0) == []
        assert await session.tail(-1) == []
    finally:
        await store.close()


async def test_session_tail_delegates_for_memory_store(tmp_path: Path) -> None:
    """Memory / fork-overlay stores fall back to ``entries()[-n:]``.

    The deque-based streaming path is file-only; for in-memory stores
    everything already sits in RAM, so ``Session.tail`` slices the
    existing entry list. Verifying the count keeps the contract honest
    without peering at private storage.
    """
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await store.open(tmp_path, "mem-tail")
        for i in range(10):
            await session.append_message("user", f"m-{i}")
        tail = await session.tail(3)
        assert len(tail) == 3
        contents = [e.payload.get("content") for e in tail if e.kind == "message"]
        assert contents == ["m-7", "m-8", "m-9"]
    finally:
        await store.close()


async def test_file_tape_store_append_is_constant_time(tmp_path: Path) -> None:
    """Cached next-id keeps per-append latency flat (PR #88 finding).

    Before the cache, ``_FileTapeStore.append`` re-parsed the whole jsonl
    file on every write to compute ``next_id``; at 100 appends the last
    call did ~100x the work of the first. This test is a loose regression
    guard — a 5x ratio is plenty to catch re-introduction of the O(n)
    path while staying robust against cold-import and filesystem jitter.
    """
    import time

    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store.open(tmp_path, "perf")
        # Warm the store / file-system caches before measuring.
        await session.append_message("user", "warm")

        timings: list[float] = []
        for i in range(100):
            t0 = time.perf_counter()
            await session.append_message("user", f"m-{i}")
            timings.append(time.perf_counter() - t0)
        # Skip the first few samples — they swallow any residual cold-path
        # cost (dir metadata caches, etc.). Tail average vs head average
        # keeps the signal robust against per-call jitter.
        head = sum(timings[5:15]) / 10
        tail = sum(timings[-10:]) / 10
        # Guard against division-by-zero on freakishly-fast runs.
        ratio = tail / head if head > 0 else 1.0
        assert ratio < 5.0, f"append latency grew {ratio:.1f}x head-to-tail (timings={timings!r})"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# tape_context selection — exercise every branch of select_messages.
# ---------------------------------------------------------------------------


async def test_context_projects_tool_call_and_result(tmp_path: Path) -> None:
    """``tool_call`` → one assistant message; ``tool_result`` → one per result, correlated."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-1")
        await session.append_message("user", "run it")
        await session.append_tool_call({
            "id": "call-a",
            "type": "function",
            "function": {"name": "echo", "arguments": "{}"},
        })
        await session.append_tool_result("call-a", {"output": "hi"})
        messages = await session.context()
        # Find the assistant tool-call message + tool result message.
        roles = [m["role"] for m in messages]
        assert "assistant" in roles
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call-a"
        assert tool_msgs[0]["name"] == "echo"
    finally:
        await store.close()


async def test_context_skips_anchors_and_non_included_events(tmp_path: Path) -> None:
    """``anchor`` entries are skipped; ``event`` entries need ``include_in_context``."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-skip")
        await session.append_message("user", "hi")
        # Observational event — default meta, must NOT appear.
        await session.append_event("bus.mirror", {"foo": "bar"})
        messages = await session.context()
        assert all("[event:" not in m.get("content", "") for m in messages)
        # anchors are present on the tape but absent from the projection.
        entries = await session.entries()
        assert any(e.kind == "anchor" for e in entries)
        assert all(m["role"] != "anchor" for m in messages)  # sanity: no "anchor" role.
    finally:
        await store.close()


async def test_context_includes_event_when_flagged(tmp_path: Path) -> None:
    """``event`` with ``include_in_context=True`` becomes a system message."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-ev")
        await session.append_event(
            "hint.injected",
            {"note": "please be terse"},
            include_in_context=True,
        )
        messages = await session.context()
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert any("[event:hint.injected]" in m["content"] for m in system_msgs)
    finally:
        await store.close()


async def test_context_tool_result_without_calls_still_renders(tmp_path: Path) -> None:
    """Orphan ``tool_result`` entries render a plain ``role=tool`` message."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-orphan")
        # Append a tool_result with no preceding tool_call.
        await session.append_tool_result("call-x", {"value": 1})
        messages = await session.context()
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert tool_msgs, messages
        # No pending call: no tool_call_id / name were attached.
        assert "tool_call_id" not in tool_msgs[0]
        assert "name" not in tool_msgs[0]
    finally:
        await store.close()


async def test_context_tool_result_ignores_non_list_results(tmp_path: Path) -> None:
    """A malformed ``tool_result.results`` value is skipped without errors."""
    from republic import TapeEntry

    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-mal")
        # Inject a malformed tool_result entry directly through the manager.
        await session.manager.append_entry(
            session.tape_name,
            TapeEntry.tool_result(results="not-a-list"),  # type: ignore[arg-type]
        )
        messages = await session.context()
        assert all(m["role"] != "tool" for m in messages)
    finally:
        await store.close()


async def test_context_render_result_handles_raw_string(tmp_path: Path) -> None:
    """A raw string inside ``results`` exercises the str branch of _render_result."""
    from republic import TapeEntry

    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "ctx-render-str")
        # Raw-string result entries — select_messages treats them as
        # opaque values and skips call correlation (no .get()).
        await session.manager.append_entry(
            session.tape_name,
            TapeEntry.tool_result(["plain-string", {"ok": True}]),
        )
        messages = await session.context()
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert tool_msgs[0]["content"] == "plain-string"
        assert '"ok"' in tool_msgs[1]["content"]
    finally:
        await store.close()


async def test_after_last_anchor_no_anchor_returns_empty(tmp_path: Path) -> None:
    """``after_last_anchor`` on a tape with only the seed anchor returns post-seed entries."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "anchor-1")
        await session.append_message("user", "after-seed")
        post = await after_last_anchor(session.manager, session.tape_name)
        # The session/start seed anchor is still the last anchor, so "after" is just the message.
        kinds = [e.kind for e in post]
        assert "message" in kinds
        assert "anchor" not in kinds
    finally:
        await store.close()


async def test_file_tape_store_next_id_cache_invalidated_on_reset(tmp_path: Path) -> None:
    """``reset()`` must drop the cached next-id so the next append restarts at 1.

    Regression guard: leaving the cache in place after a wipe would stamp
    fresh entries with stale, non-monotonic ids — and worse, it would
    collide with surviving archive rows.
    """
    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store.open(tmp_path, "cache-reset")
        await session.append_message("user", "before-reset-1")
        await session.append_message("user", "before-reset-2")
        before = await session.entries()
        ids_before = [e.id for e in before]
        # Bootstrap (anchor + event) + 2 messages → 4 entries, ids 1..4.
        assert ids_before == list(range(1, len(ids_before) + 1))

        await session.reset(archive=False)
        # After reset the file is gone and the cache entry has been dropped;
        # the next append should start from id=1 again (after the re-seeded
        # session/start anchor + its event).
        await session.append_message("user", "after-reset")
        after = await session.entries()
        ids_after = [e.id for e in after]
        assert ids_after == list(range(1, len(ids_after) + 1)), (
            f"next-id cache not invalidated on reset; ids={ids_after!r}"
        )
    finally:
        await store.close()


async def test_rename_persists_and_surfaces_via_list_sessions(tmp_path: Path) -> None:
    """``Session.rename`` writes a ``session/renamed`` anchor surfaced by ``list_sessions`` (#161)."""
    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store.open(tmp_path, "ws-rename-kernel")
        await session.append_message("user", "hi")
        await session.rename("My Favourite Chat")
        infos = await store.list_sessions(tmp_path)
        assert len(infos) == 1
        assert infos[0].name == "My Favourite Chat"
        info = await session.info()
        assert info.name == "My Favourite Chat"
    finally:
        await store.close()


async def test_rename_most_recent_wins(tmp_path: Path) -> None:
    """Stacked renames collapse to the latest one (#161)."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "rename-stack")
        await session.rename("first")
        await session.rename("second")
        await session.rename("third")
        info = await session.info()
        assert info.name == "third"
    finally:
        await store.close()


async def test_rename_rejects_blank_name(tmp_path: Path) -> None:
    """``Session.rename`` raises ``ValueError`` for whitespace-only names (#161)."""
    store = SessionStore(store=MemoryTapeStore())
    try:
        session = await _open(store, tmp_path, "rename-blank")
        with pytest.raises(ValueError, match="may not be empty"):
            await session.rename("   ")
    finally:
        await store.close()


async def test_rename_persists_across_store_reopens(tmp_path: Path) -> None:
    """Rename survives process restart (#161)."""
    tapes_dir = tmp_path / "tapes"
    store1 = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store1.open(tmp_path, "rename-persist")
        await session.append_message("user", "hi")
        await session.rename("Sticky Name")
    finally:
        await store1.close()

    store2 = SessionStore(tapes_dir=tapes_dir)
    try:
        infos = await store2.list_sessions(tmp_path)
        assert len(infos) == 1
        assert infos[0].name == "Sticky Name"
    finally:
        await store2.close()


async def test_archive_accepts_suffix_form_id(tmp_path: Path) -> None:
    """``SessionStore.archive`` resolves the suffix form surfaced by ``list_sessions`` (#161)."""
    tapes_dir = tmp_path / "tapes"
    store = SessionStore(tapes_dir=tapes_dir)
    try:
        session = await store.open(tmp_path, "archive-suffix")
        await session.append_message("user", "hi")
        infos = await store.list_sessions(tmp_path)
        sid_suffix = infos[0].session_id

        archive_path = await store.archive(sid_suffix, workspace=tmp_path)
        assert archive_path.exists()
        # After archive, the live tape is gone.
        remaining = await store.list_sessions(tmp_path)
        assert all(info.session_id != sid_suffix for info in remaining)
    finally:
        await store.close()


async def test_archive_raises_when_id_unknown(tmp_path: Path) -> None:
    """Unknown ids raise ``FileNotFoundError`` so the HTTP layer can 404 (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        with pytest.raises(FileNotFoundError):
            await store.archive("does-not-exist", workspace=tmp_path)
    finally:
        await store.close()
