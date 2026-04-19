"""Tests for the bundled ``web`` adapter plugin.

AC-bindings from ``specs/plugin-web.spec``:

* WS accept → ``test_websocket_accepts_connection``
* browser → bus → ``test_user_message_frame_emits_event``
* bus → browser → ``test_assistant_delta_reaches_client``
* broadcast fallback → ``test_kernel_event_broadcasts``
* HTTP snapshot → ``test_api_plugins_snapshot``
* shutdown → ``test_on_unload_stops_server``

The tests exercise the adapter's ASGI app directly — they do NOT
stand up uvicorn, because the ASGI contract is what the bridge
actually relies on. A separate integration-level test
(``test_on_unload_stops_server``) does boot uvicorn in-process to
prove the shutdown path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.web.plugin import WebAdapter


async def _wire(tmp_path: Path) -> tuple[WebAdapter, EventBus, KernelContext]:
    """Build an adapter wired to a fresh bus without starting uvicorn.

    The ASGI app is assembled manually so tests can speak ASGI /
    WebSocket directly via Starlette's ``TestClient``. Only the
    shutdown test boots uvicorn.
    """
    plugin = WebAdapter()
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.web-test"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )
    plugin._ctx = ctx
    plugin._app = plugin._build_app()
    return plugin, bus, ctx


async def _collect(bus: EventBus, kind: str) -> tuple[list[Event], Any]:
    """Register an observer subscription and return `(buf, sub)`."""
    buf: list[Event] = []

    async def _handler(ev: Event) -> None:
        buf.append(ev)

    sub = bus.subscribe(kind, _handler, source="observer")
    return buf, sub


async def test_websocket_accepts_connection(tmp_path: Path) -> None:
    """A WS client can connect to /ws against the adapter's ASGI app."""
    plugin, _bus, _ctx = await _wire(tmp_path)
    assert plugin._app is not None
    with TestClient(plugin._app) as client, client.websocket_connect("/ws") as ws:
        # Connection accepted — sending a malformed frame is a no-op.
        ws.send_json({"type": "noop"})


async def test_user_message_frame_emits_event(tmp_path: Path) -> None:
    """A user.message frame publishes user.message.received on the bus."""
    plugin, bus, _ctx = await _wire(tmp_path)
    assert plugin._app is not None

    received: list[Event] = []

    async def _obs(ev: Event) -> None:
        received.append(ev)

    bus.subscribe("user.message.received", _obs, source="observer")

    with TestClient(plugin._app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user.message", "text": "hi"})
        # The adapter publishes inside its receive loop; give the bus a
        # moment to drain. TestClient runs a background loop, but
        # publication is synchronous from the handler's perspective.
        deadline = 2.0
        interval = 0.02
        waited = 0.0
        while not received and waited < deadline:
            await asyncio.sleep(interval)
            waited += interval

    assert received, "user.message.received was not published within 2s"
    ev = received[0]
    assert ev.kind == "user.message.received"
    assert ev.payload["text"] == "hi"
    assert ev.session_id.startswith("ws-")


async def test_assistant_delta_reaches_client(tmp_path: Path) -> None:
    """Publishing assistant.message.delta on the WS session reaches the browser."""
    plugin, bus, ctx = await _wire(tmp_path)
    assert plugin._app is not None

    # Register the adapter's on_event as a kernel subscriber so the
    # bus routes events through it — this is what the real registry
    # does.
    async def _forward(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    for kind in plugin.subscriptions():
        bus.subscribe(kind, _forward, source=plugin.name)

    with TestClient(plugin._app) as client, client.websocket_connect("/ws") as ws:
        # Trigger a user message so the server-side knows the session id.
        ws.send_json({"type": "user.message", "text": "ping"})
        # Discover the session id the server assigned by inspecting
        # the adapter's internal map — same info the bus would carry
        # on the resulting `user.message.received` event.
        deadline = 2.0
        waited = 0.0
        while not plugin._clients and waited < deadline:
            await asyncio.sleep(0.02)
            waited += 0.02
        assert plugin._clients, "no client recorded on the adapter"
        session_id = next(iter(plugin._clients.keys()))

        await bus.publish(
            new_event(
                "assistant.message.delta",
                {"content": "hello"},
                session_id=session_id,
                source="kernel",
            )
        )
        # Drain any earlier frames the client might have seen — but
        # in this test the adapter only sends on `assistant.delta`.
        # Receive frames until we see the one we care about.
        frame = ws.receive_json()
        assert frame["type"] == "assistant.delta"
        assert frame["content"] == "hello"
        assert frame["session_id"] == session_id


async def test_kernel_event_broadcasts(tmp_path: Path) -> None:
    """A kernel.ready event fans out to every connected WS client."""
    plugin, bus, ctx = await _wire(tmp_path)
    assert plugin._app is not None

    async def _forward(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    for kind in plugin.subscriptions():
        bus.subscribe(kind, _forward, source=plugin.name)

    with (
        TestClient(plugin._app) as client,
        client.websocket_connect("/ws") as ws_a,
        client.websocket_connect("/ws") as ws_b,
    ):
        # Wait for both sessions to be recorded.
        deadline = 2.0
        waited = 0.0
        while len(plugin._clients) < 2 and waited < deadline:
            await asyncio.sleep(0.02)
            waited += 0.02
        assert len(plugin._clients) == 2

        await bus.publish(
            new_event(
                "kernel.ready",
                {"version": "0.0.1"},
                session_id="kernel",
                source="kernel",
            )
        )

        frame_a = ws_a.receive_json()
        frame_b = ws_b.receive_json()
        assert frame_a["type"] == "kernel.ready"
        assert frame_b["type"] == "kernel.ready"


async def test_api_plugins_snapshot(tmp_path: Path) -> None:
    """The /api/plugins endpoint echoes rows the adapter observed."""
    plugin, _bus, ctx = await _wire(tmp_path)
    assert plugin._app is not None

    # Directly feed a plugin.loaded event — same effect the kernel
    # would have at boot.
    await plugin.on_event(
        new_event(
            "plugin.loaded",
            {"name": "strategy-react", "version": "0.1.0", "category": "strategy"},
            session_id="kernel",
            source="kernel",
        ),
        ctx,
    )

    transport = httpx.ASGITransport(app=plugin._app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    names = [row["name"] for row in data["plugins"]]
    assert "strategy-react" in names


async def test_on_unload_stops_server(tmp_path: Path) -> None:
    """A live uvicorn server is shut down by on_unload within the budget."""
    # Pick a free port so parallel test runs don't clash.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    plugin = WebAdapter(port=port)
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.web-test-shutdown"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )

    await plugin.on_load(ctx)
    assert plugin._server_task is not None
    assert not plugin._server_task.done()

    await plugin.on_unload(ctx)
    # After unload the task must be done (completed or cancelled) and
    # the internal reference must be cleared.
    assert plugin._server is None
    assert plugin._server_task is None


async def test_plugin_inventory_is_seeded_at_load(tmp_path: Path) -> None:
    """The adapter seeds _plugin_rows from entry points at on_load time.

    Regression for PR #65: the registry emits ``plugin.loaded`` events
    for plugins loaded BEFORE the web adapter's ``on_load`` subscribes,
    so the adapter must eagerly snapshot the entry-point set to avoid a
    cold cache. See lesson #25.
    """
    plugin = WebAdapter()
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.web-test-inventory"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )
    # Don't start uvicorn; exercise only the ASGI + inventory paths.
    plugin._ctx = ctx
    plugin._app = plugin._build_app()
    plugin._prime_plugin_inventory()

    # In test env, the 4 seed plugins + web are registered. All five
    # should appear in the rows (the adapter included).
    names = set(plugin._plugin_rows.keys())
    expected = {"strategy_react", "memory_sqlite", "llm_openai", "tool_bash", "web"}
    assert expected <= names, f"missing: {expected - names}"


async def test_static_root_survives_on_load(tmp_path: Path) -> None:
    """The resolved static path must outlive on_load completion.

    Regression for PR #65: ``as_file`` is a context manager; a previous
    ``with as_file(...) as path: return path`` exited the CM before the
    StaticFiles mount ever resolved the path for a wheel-installed
    deploy.
    """
    plugin = WebAdapter(port=0)
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.web-test-static"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )
    # Use _build_app to exercise the path resolver without uvicorn.
    plugin._ctx = ctx
    app = plugin._build_app()
    # The static mount must point to a real, still-existing directory.
    assert plugin._static_path is not None
    assert plugin._static_path.is_dir()
    assert (plugin._static_path / "index.html").is_file()
    _ = app  # keep reference to avoid unused-var warning


def test_static_bundle_is_real_vite_build(tmp_path: Path) -> None:
    """``static/index.html`` must reference hashed Vite-built assets.

    Regression guard for issue #66: the shipped bundle is the output
    of ``vite build`` inside ``src/yaya/plugins/web/``; the hand-
    written placeholder preview must not be reintroduced. Vite emits
    asset names of the form ``<name>-<hash>.<ext>`` under
    ``/assets/`` — checking the HTML text for that pattern is
    enough to detect a drift without coupling to a specific hash.
    """
    from importlib.resources import files  # local import — test-only

    del tmp_path  # unused; fixture kept for parity with other tests.
    static_root = files("yaya.plugins.web") / "static"
    idx_text = (static_root / "index.html").read_text(encoding="utf-8")
    assert re.search(r"/assets/[\w.-]+-[A-Za-z0-9_-]{8,}\.js", idx_text), (
        "index.html must reference Vite-hashed JS bundles; got: " + idx_text[:500]
    )
    # The placeholder's sentinel markers (a <details> "plugins-panel"
    # section and the hand-written status copy) must be gone.
    lowered = idx_text.lower()
    assert "plugins-panel" not in lowered
    assert "preview" not in lowered


def test_ui_sidebar_present() -> None:
    """The shipped shell mounts ``<yaya-app>`` — the kimi-style root element.

    Regression guard for issue #108: the ``<yaya-chat>``-only shell has
    been wrapped in a sidebar+main layout. The entry element in
    ``static/index.html`` is now the app shell; if a future refactor
    drops it, the sidebar surfaces in the new UI vanish silently.
    """
    from importlib.resources import files as _files

    static_root = _files("yaya.plugins.web") / "static"
    idx_text = (static_root / "index.html").read_text(encoding="utf-8")
    assert "<yaya-app>" in idx_text, "index.html must mount the <yaya-app> shell element; got: " + idx_text[:500]


def test_ui_settings_chunk_present() -> None:
    """Vite emits the settings view as a dedicated chunk.

    Code-splitting keeps the chat-only code path small. This test
    asserts the chunk exists so a future config change that bundles
    the settings view back into the entry chunk fails loudly.
    """
    from importlib.resources import files as _files

    static_root = Path(str(_files("yaya.plugins.web") / "static"))
    assets = static_root / "assets"
    assert assets.is_dir(), "static/assets must exist in the shipped bundle"
    names = [p.name for p in assets.iterdir()]
    entry = [n for n in names if n.startswith("index-") and n.endswith(".js")]
    settings = [n for n in names if n.startswith("settings-view-") and n.endswith(".js")]
    assert entry, f"expected an entry chunk; found {names}"
    assert settings, f"expected a settings-view chunk; found {names}"


def test_ui_theme_tokens_present() -> None:
    """The CSS bundle declares kimi-style theme tokens + dark override.

    A theme-token regression would silently collapse the UI to
    unthemed defaults — the bundle-level assertion catches it.
    """
    from importlib.resources import files as _files

    static_root = Path(str(_files("yaya.plugins.web") / "static"))
    assets = static_root / "assets"
    css_files = [p for p in assets.iterdir() if p.suffix == ".css"]
    assert css_files, "expected at least one CSS bundle under static/assets"
    combined = "\n".join(p.read_text(encoding="utf-8") for p in css_files)
    assert "prefers-color-scheme" in combined, "CSS must declare a prefers-color-scheme rule"
    assert "--yaya-sidebar-bg" in combined, "CSS must expose the sidebar theme token"


# Silence pytest warnings about an unused async fixture pattern in this
# module-level helper. The helpers above are awaited in tests; this
# sentinel keeps the module importable under ``--strict``.
_: AsyncIterator[None] | None = None


@pytest.fixture(autouse=True)
def _quiet_uvicorn(caplog: pytest.LogCaptureFixture) -> None:
    """Silence uvicorn's noisy startup log lines during the test run."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
