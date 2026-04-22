"""Tests for ``GET /api/sessions`` on the bundled web admin router.

AC-binding: ``specs/plugin-web-config-api.spec`` gains one scenario
covering session list hydration (see ``test_sessions_list_*``).

The endpoint lists persisted tapes for the provided workspace sorted
by ``created_at`` descending so the sidebar renders newest-first.
``503`` is returned when no store was wired (mirrors the other admin
routes).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.session import SessionStore
from yaya.plugins.web.api import build_admin_router

pytestmark = pytest.mark.unit


def _build_app(
    *,
    session_store: SessionStore | None,
    workspace: Path | None,
    config_store: ConfigStore | None = None,
) -> FastAPI:
    """Return a FastAPI app that mounts only the admin router."""
    app = FastAPI()
    app.include_router(
        build_admin_router(
            registry=None,
            config_store=config_store,
            bus=None,
            session_store=session_store,
            workspace=workspace,
        )
    )
    return app


async def test_sessions_list_empty_when_store_has_no_tapes(tmp_path: Path) -> None:
    """Fresh store + empty workspace → ``{"sessions": []}``."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        assert res.status_code == 200
        assert res.json() == {"sessions": []}
    finally:
        await store.close()


async def test_sessions_list_returns_persisted_tape(tmp_path: Path) -> None:
    """After appending to a session, the endpoint lists it with entry_count > 0."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-abc123")
        await session.append_message("user", "hello", source="bdd")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        assert res.status_code == 200
        body = res.json()
        assert len(body["sessions"]) == 1
        row = body["sessions"][0]
        # ``id`` carries the tape-suffix view surfaced by SessionInfo
        # (hashed on disk — we assert only shape here, not the literal).
        assert isinstance(row["id"], str) and row["id"]
        assert row["entry_count"] >= 1
        assert "tape_name" in row
        assert "created_at" in row
    finally:
        await store.close()


async def test_sessions_list_row_includes_user_message_preview(tmp_path: Path) -> None:
    """The row must carry ``preview`` sourced from the first user message (#155)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-preview")
        await session.append_message("user", "Hello from the preview test", source="bdd")
        await session.append_message("assistant", "ack", source="bdd")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        row = res.json()["sessions"][0]
        assert row["preview"] == "Hello from the preview test"
    finally:
        await store.close()


async def test_sessions_list_preview_truncates_long_user_message(tmp_path: Path) -> None:
    """Long first-user content is trimmed with a trailing ellipsis (#155)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-long")
        await session.append_message("user", "x" * 500, source="bdd")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        preview = res.json()["sessions"][0]["preview"]
        assert preview is not None
        assert preview.endswith("…")
        assert len(preview) <= 80

    finally:
        await store.close()


async def test_sessions_list_preview_null_when_only_assistant_messages(tmp_path: Path) -> None:
    """A tape with no user message yet reports ``preview: null`` (#155)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-no-user")
        await session.append_message("assistant", "hi there", source="bdd")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        row = res.json()["sessions"][0]
        assert row["preview"] is None
    finally:
        await store.close()


async def test_sessions_list_503_when_no_store() -> None:
    """Missing store returns 503 — same degrade path as the other admin routes."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/sessions")
    assert res.status_code == 503


async def test_messages_endpoint_returns_projected_history(tmp_path: Path) -> None:
    """The endpoint projects tape entries into ``{role, content}`` rows."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-resume")
        await session.append_message("user", "first", source="bdd")
        await session.append_message("assistant", "hi", source="bdd")
        await session.append_message("user", "second", source="bdd")

        infos = await store.list_sessions(tmp_path)
        assert infos, "list_sessions should surface the persisted tape"
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/messages")
        assert res.status_code == 200
        body = res.json()
        assert body["messages"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "second"},
        ]
    finally:
        await store.close()


async def test_messages_endpoint_elides_history_before_compaction_anchor(tmp_path: Path) -> None:
    """A compaction anchor wipes prior messages and injects the summary.

    Mirrors the loop's own projection contract (see
    ``tests/kernel/test_loop.py``) so the endpoint cannot drift from
    what the agent loop will see on the next turn.
    """
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-compact")
        await session.append_message("user", "before", source="bdd")
        await session.append_message("assistant", "pre-ack", source="bdd")
        await session.handoff(
            "compaction/checkpoint", state={"kind": "compaction", "summary": "prior turns summarised"}
        )
        await session.append_message("user", "after", source="bdd")

        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/messages")
        body = res.json()
        assert body["messages"] == [
            {"role": "system", "content": "[compacted history]\nprior turns summarised"},
            {"role": "user", "content": "after"},
        ]
    finally:
        await store.close()


async def test_messages_endpoint_404_when_id_unknown(tmp_path: Path) -> None:
    """Unknown session id returns ``404``, never a silent empty list."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions/does-not-exist/messages")
        assert res.status_code == 404
    finally:
        await store.close()


async def test_messages_endpoint_503_when_no_store() -> None:
    """Missing store mirrors the list endpoint's degrade path."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/sessions/any/messages")
    assert res.status_code == 503


async def test_frames_endpoint_returns_live_shape_frames(tmp_path: Path) -> None:
    """Tool calls + results round-trip as the live WS frame shapes (#162).

    Chat-shell's reducer already handles ``assistant.done``,
    ``tool.start``, ``tool.result`` in the live path; the frames
    endpoint emits the same shapes so the hydrator walks a single
    code path.
    """
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-frames")
        await session.append_message("user", "run ls", source="bdd")
        await session.append_tool_call({"id": "t1", "name": "bash", "args": {"cmd": "ls"}})
        await session.append_tool_result("t1", {"ok": True, "value": {"stdout": "a\nb\n"}})
        await session.append_message("assistant", "Thought: done. Final Answer: listed.", source="bdd")

        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/frames")
        assert res.status_code == 200
        body = res.json()
        assert body["frames"] == [
            {"kind": "user.message", "text": "run ls"},
            {"kind": "tool.start", "id": "t1", "name": "bash", "args": {"cmd": "ls"}},
            {"kind": "tool.result", "id": "t1", "ok": True, "value": {"stdout": "a\nb\n"}},
            {
                "kind": "assistant.done",
                "content": "Thought: done. Final Answer: listed.",
                "tool_calls": [],
            },
        ]
    finally:
        await store.close()


async def test_frames_endpoint_skips_observation_user_messages(tmp_path: Path) -> None:
    """ReAct's ``Observation: ...`` user rows do not duplicate the tool card (#162)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-observation")
        await session.append_message("user", "hi", source="bdd")
        await session.append_tool_call({"id": "t1", "name": "bash", "args": {}})
        await session.append_tool_result("t1", {"ok": True, "value": "out"})
        # The ReAct strategy persists tool observations both as a
        # ``tool_result`` entry AND as a ``role="user"`` Observation
        # message (so the next LLM turn sees it). Replay must fold the
        # two into one tool card, NOT render the Observation as a
        # second user bubble.
        await session.append_message("user", "Observation: tool ok", source="bdd")
        await session.append_message("assistant", "done", source="bdd")

        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/frames")
        kinds = [f["kind"] for f in res.json()["frames"]]
        # Exactly one ``user.message`` (the real prompt), never a
        # second one derived from the Observation bubble.
        assert kinds.count("user.message") == 1
        assert "tool.start" in kinds and "tool.result" in kinds
        texts = [f["text"] for f in res.json()["frames"] if f["kind"] == "user.message"]
        assert texts == ["hi"]
    finally:
        await store.close()


async def test_frames_endpoint_elides_pre_compaction_entries(tmp_path: Path) -> None:
    """Compaction anchor wipes prior frames; same contract as /messages (#162)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-frames-compact")
        await session.append_message("user", "before", source="bdd")
        await session.append_tool_call({"id": "t1", "name": "bash", "args": {}})
        await session.append_tool_result("t1", {"ok": True, "value": "x"})
        await session.handoff(
            "compaction/checkpoint",
            state={"kind": "compaction", "summary": "prior turns summarised"},
        )
        await session.append_message("user", "after", source="bdd")

        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/frames")
        # Only the post-anchor frame survives; pre-anchor entries and
        # the compaction marker itself are dropped.
        assert res.json()["frames"] == [
            {"kind": "user.message", "text": "after"},
        ]
    finally:
        await store.close()


async def test_frames_endpoint_404_when_id_unknown(tmp_path: Path) -> None:
    """Unknown session id returns 404 — mirrors /messages (#162)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions/does-not-exist/frames")
        assert res.status_code == 404
    finally:
        await store.close()


async def test_frames_endpoint_503_when_no_store() -> None:
    """Missing store returns 503 — mirrors /messages (#162)."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/sessions/any/frames")
    assert res.status_code == 503


async def test_frames_endpoint_tool_result_carries_error_field(tmp_path: Path) -> None:
    """Failed tool calls surface ``error`` + ``ok=false`` on the replay frame (#162)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-frames-err")
        await session.append_tool_call({"id": "t1", "name": "bash", "args": {}})
        await session.append_tool_result("t1", {"ok": False, "error": "boom"})

        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{sid}/frames")
        frames = res.json()["frames"]
        result_frame = next(f for f in frames if f["kind"] == "tool.result")
        assert result_frame["ok"] is False
        assert result_frame["error"] == "boom"
        assert "value" not in result_frame
    finally:
        await store.close()


async def test_delete_session_archives_the_tape(tmp_path: Path) -> None:
    """DELETE archives the tape and removes it from the live list (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-delete")
        await session.append_message("user", "bye", source="bdd")
        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.delete(f"/api/sessions/{sid}")
            assert res.status_code == 204
            # Follow-up listing no longer includes the tape.
            listed = await client.get("/api/sessions")
        assert all(row["id"] != sid for row in listed.json()["sessions"])
        # The archive dump is recoverable from disk.
        archive_dir = tmp_path / "tapes" / ".archive"
        assert archive_dir.is_dir()
        assert any(archive_dir.iterdir()), "archive directory should carry the dump"
    finally:
        await store.close()


async def test_delete_session_404_when_id_unknown(tmp_path: Path) -> None:
    """DELETE returns 404 when the id does not resolve to any tape (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.delete("/api/sessions/does-not-exist")
        assert res.status_code == 404
    finally:
        await store.close()


async def test_delete_session_503_when_no_store() -> None:
    """DELETE returns 503 when the store / workspace was not wired (#161)."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.delete("/api/sessions/anything")
    assert res.status_code == 503


async def test_patch_session_writes_name_and_is_reflected_in_list(tmp_path: Path) -> None:
    """PATCH persists ``name`` and ``GET /api/sessions`` surfaces it (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-rename")
        await session.append_message("user", "hello", source="bdd")
        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.patch(f"/api/sessions/{sid}", json={"name": "Grocery list"})
            assert res.status_code == 200, res.text
            assert res.json()["name"] == "Grocery list"
            listed = await client.get("/api/sessions")
        row = next(r for r in listed.json()["sessions"] if r["id"] == sid)
        assert row["name"] == "Grocery list"
    finally:
        await store.close()


async def test_patch_session_most_recent_rename_wins(tmp_path: Path) -> None:
    """Stacked renames collapse to the latest one (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-rename-stack")
        await session.append_message("user", "hi", source="bdd")
        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.patch(f"/api/sessions/{sid}", json={"name": "First"})
            res = await client.patch(f"/api/sessions/{sid}", json={"name": "Second"})
        assert res.status_code == 200
        assert res.json()["name"] == "Second"
    finally:
        await store.close()


async def test_patch_session_404_when_id_unknown(tmp_path: Path) -> None:
    """PATCH returns 404 when the id does not resolve to any tape (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.patch("/api/sessions/nope", json={"name": "whatever"})
        assert res.status_code == 404
    finally:
        await store.close()


async def test_patch_session_400_when_name_empty(tmp_path: Path) -> None:
    """PATCH rejects blank names with 400 rather than writing an empty anchor (#161)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-empty-name")
        await session.append_message("user", "hi", source="bdd")
        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.patch(f"/api/sessions/{sid}", json={"name": "   "})
        assert res.status_code == 400
    finally:
        await store.close()


async def test_patch_session_422_when_name_exceeds_max_length(tmp_path: Path) -> None:
    """PATCH caps ``name`` at 200 chars (#161 review fixup)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-long-name")
        await session.append_message("user", "hi", source="bdd")
        infos = await store.list_sessions(tmp_path)
        sid = infos[0].session_id

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.patch(f"/api/sessions/{sid}", json={"name": "x" * 201})
        # Pydantic field validation surfaces as 422 by default.
        assert res.status_code == 422
    finally:
        await store.close()


async def test_patch_session_503_when_no_store() -> None:
    """PATCH returns 503 when the store / workspace was not wired (#161)."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.patch("/api/sessions/anything", json={"name": "x"})
    assert res.status_code == 503


async def test_sessions_list_sorted_by_created_at_desc(tmp_path: Path) -> None:
    """Newer sessions appear before older ones."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        older = await store.open(tmp_path, "ws-old")
        await older.append_message("user", "first", source="bdd")
        newer = await store.open(tmp_path, "ws-new")
        await newer.append_message("user", "second", source="bdd")

        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        rows = res.json()["sessions"]
        ids = [r["id"] for r in rows]
        # Both tapes visible; order by created_at desc (newer first).
        assert len(ids) == 2
        timestamps = [r["created_at"] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# #163 — per-session provider anchor + single-session endpoint
# ---------------------------------------------------------------------------


async def test_sessions_list_row_carries_provider_and_model(tmp_path: Path) -> None:
    """AC #163: each row exposes provider + model from the latest anchor."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-provider")
        await session.append_message("user", "hi", source="bdd")
        await session.append_turn_provider("llm-openai", "gpt-4o-mini")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        assert res.status_code == 200
        row = res.json()["sessions"][0]
        assert row["provider"] == "llm-openai"
        assert row["model"] == "gpt-4o-mini"
    finally:
        await store.close()


async def test_sessions_list_row_provider_null_for_legacy_tape(tmp_path: Path) -> None:
    """Legacy tapes without the anchor surface ``null`` provider/model (#163)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        session = await store.open(tmp_path, "ws-legacy")
        await session.append_message("user", "hi", source="bdd")
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions")
        row = res.json()["sessions"][0]
        assert row["provider"] is None
        assert row["model"] is None
    finally:
        await store.close()


async def test_single_session_endpoint_available_when_provider_still_configured(tmp_path: Path) -> None:
    """GET /api/sessions/{id} → available=True when the historical provider exists (#163)."""
    bus = EventBus()
    config_store = await ConfigStore.open(bus=bus, path=tmp_path / "cfg.db")
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        # Seed a provider instance so the availability check resolves True.
        await config_store.set("providers.llm-openai.plugin", "llm-openai")
        await config_store.set("providers.llm-openai.label", "OpenAI default")
        await config_store.set("provider", "llm-openai")
        session = await store.open(tmp_path, "ws-avail")
        await session.append_message("user", "hi", source="bdd")
        await session.append_turn_provider("llm-openai", "gpt-4o-mini")
        # Resolve the suffix id that the API uses.
        infos = await store.list_sessions(tmp_path)
        session_id = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path, config_store=config_store)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{session_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["provider"] == "llm-openai"
        assert body["model"] == "gpt-4o-mini"
        avail = body["provider_availability"]
        assert avail["available"] is True
        assert avail["active"] == "llm-openai"
        assert "llm-openai" in avail["known_providers"]
    finally:
        await store.close()
        await config_store.close()
        await bus.close()


async def test_single_session_endpoint_available_false_when_provider_gone(tmp_path: Path) -> None:
    """Historical provider removed → available=False so the UI can banner (#163)."""
    bus = EventBus()
    config_store = await ConfigStore.open(bus=bus, path=tmp_path / "cfg.db")
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        # Only "llm-openai" is configured; historical anchor references "llm-gone".
        await config_store.set("providers.llm-openai.plugin", "llm-openai")
        await config_store.set("provider", "llm-openai")
        session = await store.open(tmp_path, "ws-missing")
        await session.append_message("user", "hi", source="bdd")
        await session.append_turn_provider("llm-gone", "old-model")
        infos = await store.list_sessions(tmp_path)
        session_id = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path, config_store=config_store)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{session_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["provider"] == "llm-gone"
        avail = body["provider_availability"]
        assert avail["available"] is False
        assert avail["active"] == "llm-openai"
        assert "llm-openai" in avail["known_providers"]
        assert "llm-gone" not in avail["known_providers"]
    finally:
        await store.close()
        await config_store.close()
        await bus.close()


async def test_single_session_endpoint_available_true_for_same_provider_different_model(tmp_path: Path) -> None:
    """Model mismatch on the same instance does NOT trip the banner (#163).

    Documented design call: users routinely upgrade the model string on
    an existing instance. Flagging that as "unavailable" would nag every
    legitimate upgrade. The narrower "provider + model must match" rule
    can land later if misuse shows up.
    """
    bus = EventBus()
    config_store = await ConfigStore.open(bus=bus, path=tmp_path / "cfg.db")
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        await config_store.set("providers.llm-openai.plugin", "llm-openai")
        await config_store.set("providers.llm-openai.model", "gpt-4o")  # upgraded
        await config_store.set("provider", "llm-openai")
        session = await store.open(tmp_path, "ws-model-change")
        await session.append_message("user", "hi", source="bdd")
        # Historical anchor references the OLD model on the SAME instance.
        await session.append_turn_provider("llm-openai", "gpt-4o-mini")
        infos = await store.list_sessions(tmp_path)
        session_id = infos[0].session_id
        app = _build_app(session_store=store, workspace=tmp_path, config_store=config_store)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get(f"/api/sessions/{session_id}")
        assert res.status_code == 200
        avail = res.json()["provider_availability"]
        assert avail["available"] is True
    finally:
        await store.close()
        await config_store.close()
        await bus.close()


async def test_single_session_endpoint_404_when_id_unknown(tmp_path: Path) -> None:
    """Single-session endpoint 404s for an unknown id (#163)."""
    store = SessionStore(tapes_dir=tmp_path / "tapes")
    try:
        app = _build_app(session_store=store, workspace=tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/sessions/does-not-exist")
        assert res.status_code == 404
    finally:
        await store.close()


async def test_single_session_endpoint_503_when_no_store() -> None:
    """Missing store → 503 (#163)."""
    app = _build_app(session_store=None, workspace=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/sessions/anything")
    assert res.status_code == 503
