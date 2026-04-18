"""Pytest-bdd execution of specs/plugin-web.spec scenarios.

The Gherkin text in ``features/plugin-web.feature`` is the
authoritative BDD contract for the bundled web adapter. Each step
exercises the real :class:`WebAdapter` wired to a real
:class:`EventBus`; the adapter's ASGI app is driven through
Starlette's ``TestClient`` so WebSocket accept + send/recv go through
the same code path a browser would hit in production.

Step defs are synchronous (pytest-bdd shape); async work is dispatched
onto an :class:`anyio.from_thread.BlockingPortal` owned by the test
client so bus publishes happen in the same event loop that owns the
WebSocket — mixing loops here deadlocks the portal during teardown.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from starlette.testclient import TestClient

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.web.plugin import WebAdapter

pytestmark = pytest.mark.integration

FEATURE_FILE = Path(__file__).parent / "features" / "plugin-web.feature"
scenarios(str(FEATURE_FILE))


class _WebCtx:
    """Per-scenario state for the web adapter BDD steps.

    The ``TestClient`` owns the asyncio event loop the adapter's ASGI
    app runs under. All bus publishes must be scheduled onto that
    same loop via :attr:`portal_call` — calling ``asyncio.run`` here
    would spawn a rival loop and deadlock on teardown.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.adapter = WebAdapter()
        self.bus = EventBus()
        self.ctx = KernelContext(
            bus=self.bus,
            logger=logging.getLogger("plugin.web-bdd"),
            config={},
            state_dir=tmp_path,
            plugin_name=self.adapter.name,
        )
        self.adapter._ctx = self.ctx
        self.adapter._app = self.adapter._build_app()

        async def _forward(ev: Event) -> None:
            await self.adapter.on_event(ev, self.ctx)

        for kind in self.adapter.subscriptions():
            self.bus.subscribe(kind, _forward, source=self.adapter.name)

        self.received: dict[str, list[Event]] = {}
        self.client_ws: list[Any] = []
        self.test_client: TestClient | None = None
        self.last_response: httpx.Response | None = None
        # Separate adapter instance used by the shutdown scenario that
        # boots a real uvicorn server — kept apart so it doesn't try to
        # share the ASGI-driven app.
        self.live_adapter: WebAdapter | None = None

    def ensure_test_client(self) -> TestClient:
        if self.test_client is None:
            assert self.adapter._app is not None
            tc = TestClient(self.adapter._app)
            tc.__enter__()
            self.test_client = tc
        return self.test_client

    def portal_call(self, fn: Any, /, *args: Any) -> Any:
        """Run ``fn(*args)`` on the TestClient's event loop.

        The portal is populated once the TestClient enters its
        context; it is the only safe way to publish on ``self.bus``
        from inside a synchronous step def.
        """
        tc = self.ensure_test_client()
        portal = tc.portal
        assert portal is not None, "TestClient portal not ready"
        return portal.call(fn, *args)

    def observe(self, kind: str) -> None:
        buf: list[Event] = []
        self.received[kind] = buf

        async def _handler(ev: Event) -> None:
            buf.append(ev)

        self.bus.subscribe(kind, _handler, source="observer")

    def close(self) -> None:
        # Close WS first so TestClient portal drains cleanly.
        import contextlib

        for ws in self.client_ws:
            with contextlib.suppress(Exception):
                ws.__exit__(None, None, None)
        if self.test_client is not None:
            with contextlib.suppress(Exception):
                self.test_client.__exit__(None, None, None)


@pytest.fixture
def web_ctx(tmp_path: Path) -> Generator[_WebCtx]:
    state = _WebCtx(tmp_path)
    try:
        yield state
    finally:
        state.close()


# -- Givens ---------------------------------------------------------------


@given("a loaded web adapter plugin")
def _a_loaded_adapter(web_ctx: _WebCtx) -> None:
    assert web_ctx.adapter._app is not None


@given("a loaded web adapter plugin with a websocket client connected")
def _adapter_with_client(web_ctx: _WebCtx) -> None:
    web_ctx.observe("user.message.received")
    tc = web_ctx.ensure_test_client()
    ws = tc.websocket_connect("/ws")
    ws.__enter__()
    web_ctx.client_ws.append(ws)


@given("a loaded web adapter plugin with a websocket client connected on a session")
def _adapter_with_client_session(web_ctx: _WebCtx) -> None:
    tc = web_ctx.ensure_test_client()
    ws = tc.websocket_connect("/ws")
    ws.__enter__()
    web_ctx.client_ws.append(ws)
    # Send a bootstrap so the adapter records the session id.
    ws.send_json({"type": "user.message", "text": "bootstrap"})
    _wait_for(lambda: bool(web_ctx.adapter._clients))


@given("a loaded web adapter plugin with two websocket clients connected")
def _adapter_two_clients(web_ctx: _WebCtx) -> None:
    tc = web_ctx.ensure_test_client()
    for _ in range(2):
        ws = tc.websocket_connect("/ws")
        ws.__enter__()
        web_ctx.client_ws.append(ws)
    _wait_for(lambda: len(web_ctx.adapter._clients) >= 2)


@given("a loaded web adapter plugin that observed a plugin.loaded event")
def _adapter_observed_plugin_loaded(web_ctx: _WebCtx) -> None:
    ev = new_event(
        "plugin.loaded",
        {"name": "strategy-react", "version": "0.1.0", "category": "strategy"},
        session_id="kernel",
        source="kernel",
    )

    async def _emit() -> None:
        await web_ctx.adapter.on_event(ev, web_ctx.ctx)

    web_ctx.portal_call(_emit)


@given("a loaded web adapter plugin with an active uvicorn server")
def _adapter_live_uvicorn(web_ctx: _WebCtx) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    live = WebAdapter(port=port)
    live_ctx = KernelContext(
        bus=web_ctx.bus,
        logger=logging.getLogger("plugin.web-bdd-live"),
        config={},
        state_dir=web_ctx.tmp_path,
        plugin_name=live.name,
    )
    # on_load spawns a uvicorn task; it must live in the SAME event
    # loop that on_unload will await on. We use the TestClient's
    # portal so both calls share one loop across steps.
    web_ctx.portal_call(live.on_load, live_ctx)
    web_ctx.live_adapter = live
    web_ctx.ctx = live_ctx


# -- Whens ----------------------------------------------------------------


@when("a websocket client connects to the ws route")
def _client_connects(web_ctx: _WebCtx) -> None:
    tc = web_ctx.ensure_test_client()
    ws = tc.websocket_connect("/ws")
    ws.__enter__()
    web_ctx.client_ws.append(ws)


@when(parsers.parse("the client sends a user.message frame carrying text {text}"))
def _client_sends_user_message(web_ctx: _WebCtx, text: str) -> None:
    ws = web_ctx.client_ws[-1]
    ws.send_json({"type": "user.message", "text": text})
    _wait_for(lambda: bool(web_ctx.received.get("user.message.received")))


@when("an assistant.message.delta event is published for that session")
def _publish_assistant_delta(web_ctx: _WebCtx) -> None:
    session_id = next(iter(web_ctx.adapter._clients.keys()))

    async def _pub() -> None:
        await web_ctx.bus.publish(
            new_event(
                "assistant.message.delta",
                {"content": "hello"},
                session_id=session_id,
                source="kernel",
            )
        )

    web_ctx.portal_call(_pub)


@when("a kernel.ready event is published on the kernel session")
def _publish_kernel_ready(web_ctx: _WebCtx) -> None:
    async def _pub() -> None:
        await web_ctx.bus.publish(
            new_event(
                "kernel.ready",
                {"version": "0.0.1"},
                session_id="kernel",
                source="kernel",
            )
        )

    web_ctx.portal_call(_pub)


@when("a client issues a GET request to api plugins")
def _client_gets_plugins(web_ctx: _WebCtx) -> None:
    assert web_ctx.adapter._app is not None
    transport = httpx.ASGITransport(app=web_ctx.adapter._app)

    async def _do() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/plugins")

    web_ctx.last_response = web_ctx.portal_call(_do)


@when("on_unload is awaited")
def _await_on_unload(web_ctx: _WebCtx) -> None:
    live = web_ctx.live_adapter
    assert live is not None
    web_ctx.portal_call(live.on_unload, web_ctx.ctx)


# -- Thens ---------------------------------------------------------------


@then("the connection is accepted and bound to 127.0.0.1")
def _connection_accepted(web_ctx: _WebCtx) -> None:
    ws = web_ctx.client_ws[-1]
    ws.send_json({"type": "noop"})


@then(parsers.parse("a user.message.received event is observed on the bus with text {text}"))
def _observed_user_message(web_ctx: _WebCtx, text: str) -> None:
    events = web_ctx.received.get("user.message.received", [])
    assert events, "no user.message.received event observed"
    assert events[0].payload["text"] == text


@then("the client receives an assistant.delta frame with the same content")
def _client_receives_assistant_delta(web_ctx: _WebCtx) -> None:
    ws = web_ctx.client_ws[-1]
    frame = cast("dict[str, Any]", ws.receive_json())
    assert frame["type"] == "assistant.delta"
    assert frame["content"] == "hello"


@then("both clients receive the kernel.ready frame")
def _both_receive_kernel_ready(web_ctx: _WebCtx) -> None:
    for ws in web_ctx.client_ws:
        frame = cast("dict[str, Any]", ws.receive_json())
        assert frame["type"] == "kernel.ready"


@then("the response body carries a plugins list with the observed row")
def _response_has_plugin(web_ctx: _WebCtx) -> None:
    assert web_ctx.last_response is not None
    assert web_ctx.last_response.status_code == 200
    data = web_ctx.last_response.json()
    names = [row["name"] for row in data["plugins"]]
    assert "strategy-react" in names


@then("the uvicorn server task completes and clients are closed")
def _uvicorn_stopped(web_ctx: _WebCtx) -> None:
    live = web_ctx.live_adapter
    assert live is not None
    assert live._server is None
    assert live._server_task is None


# ---------------------------------------------------------------------------
# Scenario: Shipped static bundle is a real Vite build
# ---------------------------------------------------------------------------


@given("the packaged web plugin static directory", target_fixture="static_dir")
def _static_dir() -> Path:
    """Resolve the in-source ``static/`` via ``importlib.resources``."""
    from importlib.resources import files

    resource = files("yaya.plugins.web") / "static"
    return Path(str(resource))


@when(
    "its index.html is inspected",
    target_fixture="index_html_text",
)
def _read_index_html(static_dir: Path) -> str:
    return (static_dir / "index.html").read_text(encoding="utf-8")


@then("it references Vite-hashed JS assets and no placeholder markers remain")
def _assert_vite_bundle(index_html_text: str) -> None:
    import re

    assert re.search(r"/assets/[\w.-]+-[A-Za-z0-9_-]{8,}\.js", index_html_text), (
        "index.html must reference Vite-hashed JS bundles; got: " + index_html_text[:500]
    )
    lowered = index_html_text.lower()
    assert "plugins-panel" not in lowered
    assert "preview" not in lowered


# -- helpers --------------------------------------------------------------


def _wait_for(pred: Any, *, timeout: float = 2.0, interval: float = 0.02) -> None:
    """Poll ``pred`` until true or timeout — synchronous for step defs."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate never satisfied within {timeout}s")
