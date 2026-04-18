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
