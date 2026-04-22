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

from yaya.kernel.session import SessionStore
from yaya.plugins.web.api import build_admin_router

pytestmark = pytest.mark.unit


def _build_app(*, session_store: SessionStore | None, workspace: Path | None) -> FastAPI:
    """Return a FastAPI app that mounts only the admin router."""
    app = FastAPI()
    app.include_router(
        build_admin_router(
            registry=None,
            config_store=None,
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
