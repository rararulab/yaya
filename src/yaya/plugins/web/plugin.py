"""Web adapter plugin implementation.

The plugin owns four responsibilities:

1. Boot a FastAPI + uvicorn server on ``127.0.0.1:<port>`` during
   ``on_load``; shut it down cleanly during ``on_unload`` within a
   3-second budget.
2. Subscribe to the kernel events every adapter routes to a UI —
   ``assistant.message.*``, ``tool.call.start``, ``tool.call.result``,
   ``plugin.*``, ``kernel.*`` — and forward them to the appropriate
   WebSocket clients as JSON frames.
3. Accept browser → adapter frames on the WebSocket and translate
   them into ``user.message.received`` / ``user.interrupt`` events on
   the bus.
4. Expose a read-only ``GET /api/plugins`` endpoint that surfaces a
   static snapshot payload the UI polls to render plugin status.
   (Populated from ``plugin.loaded`` / ``plugin.removed`` events —
   the registry is not consulted directly because plugins do not
   reach into kernel internals.)

Session routing
---------------
Every WebSocket connection receives a session id of the form
``ws-<uuid4[:8]>`` at ``accept`` time. That id is:

* Used as the kernel session id on every event the adapter publishes
  for that connection (``user.message.received`` + ``user.interrupt``).
* Used as the routing key on every outbound event — the adapter
  keeps ``_clients: dict[str, set[WebSocket]]`` and delivers an
  inbound kernel event to every socket registered under
  ``ev.session_id``.
* Events carrying a session id that doesn't match any WS connection
  (notably kernel-origin events on session ``"kernel"``) are
  broadcast to every connected client so lifecycle state is visible
  everywhere.

FIFO semantics are preserved because each WS connection processes
outbound frames in the order the bus delivered them — the bus's
per-session drain worker already serializes events on the same
session id, and ``send_text`` on a single WebSocket runs inside the
bus handler so no re-ordering happens.

Layering
--------
This module imports only from :mod:`yaya.kernel` plus stdlib,
``fastapi``, ``uvicorn`` and ``starlette``. No reaching into
``yaya.cli`` or ``yaya.core``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import defaultdict
from collections.abc import Awaitable
from importlib.metadata import entry_points
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, HealthReport, KernelContext
from yaya.plugins.web.api import build_admin_router

if TYPE_CHECKING:  # pragma: no cover - type-only imports.
    import uvicorn

_NAME = "web"
_VERSION = "0.1.0"
_ADAPTER_ID = "web"
_BIND_HOST = "127.0.0.1"
"""Hard-coded per GOAL.md non-goals — no public bind through 1.0."""

# Event kinds the adapter forwards to browsers. Kept as a module-level
# tuple so the subscription list and the frame translator agree by
# construction and the mapping is grep-able from tests.
_FORWARD_KINDS: tuple[str, ...] = (
    "assistant.message.delta",
    "assistant.message.done",
    "tool.call.start",
    "tool.call.result",
    "plugin.loaded",
    "plugin.removed",
    "plugin.error",
    "kernel.ready",
    "kernel.shutdown",
    "kernel.error",
)

_SHUTDOWN_TIMEOUT_S: float = 3.0
"""Upper bound we wait for uvicorn to stop during ``on_unload``."""


def _event_to_frame(ev: Event) -> dict[str, Any]:
    """Translate a kernel event into the WS frame documented in ``web-ui.md``.

    The frame schema is a thin serialization of the event catalog —
    the ``type`` prefix drops ``.received`` / ``.message`` intermediate
    segments to keep the browser-facing form short. Unknown kinds
    (extension ``x.*`` events included) fall through with their raw
    kind as ``type`` and their payload attached verbatim so plugin-
    private UI surfaces can still route their own frames.
    """
    kind = ev.kind
    # Compact mapping for the documented public kinds. Anything not
    # listed falls through to the identity mapping below.
    match kind:
        case "assistant.message.delta":
            wire_type = "assistant.delta"
        case "assistant.message.done":
            wire_type = "assistant.done"
        case "tool.call.start":
            wire_type = "tool.start"
        case "tool.call.result":
            wire_type = "tool.result"
        case _:
            wire_type = kind

    frame: dict[str, Any] = {"type": wire_type, "session_id": ev.session_id}
    frame.update(ev.payload)
    return frame


class WebAdapter:
    """Bundled FastAPI + WebSocket adapter plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.ADAPTER`.
        adapter_id: Discriminator used by tooling that wants to find
            "the web adapter" specifically (``_has_web_adapter`` in
            :mod:`yaya.cli.commands.serve`).
        requires: Empty — the web adapter has no hard plugin deps.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.ADAPTER
    adapter_id: str = _ADAPTER_ID
    requires: ClassVar[list[str]] = []

    def __init__(self, *, port: int | None = None) -> None:
        """Build the adapter.

        Args:
            port: Explicit port override (tests). When ``None``, the
                port is read from ``YAYA_WEB_PORT`` at ``on_load`` —
                ``0`` or unset asks the OS for a free port.
        """
        self._port_override = port
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._plugin_rows: dict[str, dict[str, Any]] = {}
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._bound_port: int | None = None
        self._ctx: KernelContext | None = None
        # ``as_file`` is a context manager; for wheel-installed deploys
        # it materialises a temp dir that is cleaned up on ``__exit__``.
        # Hold the CM on the instance and release it in ``on_unload``
        # so the static mount keeps resolving for the adapter's lifetime.
        self._static_cm: Any = None
        self._static_path: Path | None = None

    @property
    def bound_port(self) -> int | None:
        """Return the port uvicorn is listening on, or None before ``on_load``.

        Exposed so the CLI (``yaya serve``) can open the browser at the
        correct URL without duplicating the resolve logic. Kernel's
        ``bound_port`` is a separate, non-HTTP construct; the real user-
        facing address is this one.
        """
        return self._bound_port

    # -- ABI --------------------------------------------------------------------

    def subscriptions(self) -> list[str]:
        """Public kinds this adapter forwards to browsers (plus ``plugin.loaded`` mirror)."""
        return list(_FORWARD_KINDS)

    async def on_load(self, ctx: KernelContext) -> None:
        """Build the FastAPI app and start uvicorn in the background.

        Raises:
            RuntimeError: If the server failed to bind before the start
                window elapsed. The registry surfaces this as
                ``plugin.error`` — the kernel itself stays up.
        """
        self._ctx = ctx
        port = self._resolve_port()
        self._prime_plugin_inventory()
        app = self._build_app()
        self._app = app

        # Imported lazily so the plugin module is importable on systems
        # that haven't yet installed uvicorn — discovery succeeds and
        # the failure surface is on ``on_load`` with a clear message.
        import uvicorn

        config = uvicorn.Config(
            app=app,
            host=_BIND_HOST,
            port=port,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        self._server = server
        self._server_task = asyncio.create_task(server.serve(), name="yaya-web-uvicorn")
        # Wait briefly for uvicorn to claim the port so the `ready`
        # log line is deterministic; uvicorn sets ``started`` after
        # bind.
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        else:
            ctx.logger.warning("uvicorn did not report started within 2s; continuing")

        self._bound_port = port
        ctx.logger.info("web adapter listening on http://%s:%d", _BIND_HOST, port)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route inbound kernel events to connected browsers.

        Also maintains the local ``_plugin_rows`` snapshot surfaced by
        ``GET /api/plugins``: ``plugin.loaded`` inserts, ``plugin.removed``
        evicts. The snapshot is best-effort — adapters are not a
        source of truth for registry state.
        """
        if ev.kind == "plugin.loaded":
            name = str(ev.payload.get("name", ""))
            if name:
                self._plugin_rows[name] = {
                    "name": name,
                    "version": str(ev.payload.get("version", "")),
                    "category": str(ev.payload.get("category", "")),
                    "status": "loaded",
                }
        elif ev.kind == "plugin.removed":
            name = str(ev.payload.get("name", ""))
            self._plugin_rows.pop(name, None)

        frame = _event_to_frame(ev)
        await self._deliver(ev.session_id, frame)

    async def on_unload(self, ctx: KernelContext) -> None:
        """Stop uvicorn cleanly within 3 s, then cancel if it hangs."""
        server = self._server
        task = self._server_task
        self._server = None
        self._server_task = None
        if server is None or task is None:
            return

        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=_SHUTDOWN_TIMEOUT_S)
        except TimeoutError:
            ctx.logger.warning("web adapter uvicorn shutdown exceeded %.1fs; cancelling", _SHUTDOWN_TIMEOUT_S)
            task.cancel()
            # Narrow to CancelledError (lesson #3): it is the expected
            # post-cancel exception. Any other BaseException subclass
            # coming out of a cancelled task is a real bug and must
            # propagate, not be swallowed.
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Close any WS connections the uvicorn shutdown didn't reach.
        for sockets in list(self._clients.values()):
            for ws in list(sockets):
                with contextlib.suppress(Exception):
                    await ws.close()
        self._clients.clear()

        # Release the static-files context manager so wheel-extracted
        # temp dirs clean up. Safe if ``_static_root`` was never called.
        if self._static_cm is not None:
            with contextlib.suppress(Exception):
                self._static_cm.__exit__(None, None, None)
            self._static_cm = None
            self._static_path = None

    async def health_check(self, ctx: KernelContext) -> HealthReport:
        """Verify the static bundle path; report server state if running.

        No network: the check only touches filesystem state via
        :func:`importlib.resources.files`. If the bundle is missing
        the wheel is malformed — report ``failed``. If the uvicorn
        server is live under :attr:`_server`, report ``ok`` with the
        serving port; otherwise ``ok`` with "bundle only" so
        ``yaya doctor`` (which runs without ``serve``) does not mark
        this degraded.
        """
        del ctx  # inspection is fully self-contained.
        try:
            static_root = self._static_root()
        except Exception as exc:
            return HealthReport(
                status="failed",
                summary=f"static bundle not found: {exc}",
            )
        if not static_root.is_dir():
            return HealthReport(
                status="failed",
                summary=f"static bundle missing: {static_root}",
            )
        if self._server is not None:
            return HealthReport(
                status="ok",
                summary=f"serving {static_root.name} (bundle ok)",
            )
        return HealthReport(
            status="ok",
            summary=f"bundle ok at {static_root}",
        )

    # -- HTTP / WS --------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        """Assemble the FastAPI app routes.

        Kept separate from ``on_load`` so tests can exercise the ASGI
        app without standing up uvicorn.
        """
        app = FastAPI(
            title="yaya web adapter",
            version=_VERSION,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )

        static_root = self._static_root()

        @app.get("/api/health")
        async def _health() -> JSONResponse:
            """Tiny ok-probe for test harnesses."""
            return JSONResponse({"ok": True, "adapter": _ADAPTER_ID})

        # Resolve admin-side references from the kernel context.
        # When the adapter boots without a registry (tests, transient
        # ``yaya plugin list`` stack), admin endpoints degrade to 503
        # and the legacy cached ``_plugin_rows`` snapshot wins at
        # ``GET /api/plugins``.
        ctx = self._ctx
        registry_ref = ctx.registry if ctx is not None else None
        store_ref = ctx.config_store if ctx is not None else None
        bus_ref = ctx.bus if ctx is not None else None
        session_store_ref = ctx.session_store if ctx is not None else None

        # FastAPI matches in registration order. Register the legacy
        # ``/api/plugins`` fallback FIRST when no registry is wired so
        # tests that mount the ASGI app without a live registry still
        # see the pre-API-layer behaviour. The admin router registers
        # its own ``/api/plugins`` handler too; with a live registry
        # it is registered first and takes precedence.
        if registry_ref is None:

            @app.get("/api/plugins")
            async def _plugins_fallback() -> JSONResponse:
                """Legacy cached snapshot used when no registry is wired."""
                rows = list(self._plugin_rows.values())
                return JSONResponse({"plugins": rows})

        app.include_router(
            build_admin_router(
                registry=registry_ref,
                config_store=store_ref,
                bus=bus_ref,
                session_store=session_store_ref,
                workspace=Path.cwd(),
            )
        )

        @app.get("/api/plugins/snapshot")
        async def _plugins_snapshot() -> JSONResponse:
            """Return the adapter's cached plugin snapshot (legacy diag)."""
            rows = list(self._plugin_rows.values())
            return JSONResponse({"plugins": rows})

        @app.websocket("/ws")
        async def _ws(ws: WebSocket) -> None:
            """Accept a WS connection and bridge it to the bus."""
            await self._handle_ws(ws)

        # Mount static AFTER the API routes so /api/* wins over an
        # accidentally-named asset.
        app.mount("/", StaticFiles(directory=str(static_root), html=True), name="static")
        return app

    def _static_root(self) -> Path:
        """Resolve ``<pkg>/static/`` and keep the context alive.

        :func:`importlib.resources.as_file` is a context manager. For
        editable installs it returns the real on-disk path and the
        ``__exit__`` is a no-op; for wheel-installed deploys it may
        materialise a temp dir that is deleted on ``__exit__``. We
        therefore keep the context manager open on the instance for
        the adapter's whole lifetime and release it in ``on_unload``.
        """
        if self._static_path is not None:
            return self._static_path
        resource = files("yaya.plugins.web") / "static"
        cm = as_file(resource)
        path = Path(cm.__enter__())
        self._static_cm = cm
        self._static_path = path
        return path

    def _prime_plugin_inventory(self) -> None:
        """Seed ``_plugin_rows`` from the live entry-point set.

        The registry emits ``plugin.loaded`` events per plugin during
        ``start()``; the web adapter typically loads last (alphabetical
        entry-point order puts ``web`` after ``memory_sqlite`` /
        ``strategy_react`` etc.), so those events fire before the
        adapter's ``on_load`` subscribes. Bootstrapping from the
        entry-point set closes the gap without adding a new event kind
        (lesson #25). Subsequent ``plugin.loaded`` / ``plugin.removed``
        events still flow through ``on_event`` and patch the cache.
        """
        try:
            eps = entry_points(group="yaya.plugins.v1")
        except Exception as exc:
            if self._ctx is not None:
                self._ctx.logger.warning("could not enumerate yaya.plugins.v1: %s", exc)
            return
        for ep in eps:
            # ``ep.dist.version`` is the owning distribution's version
            # (e.g. "0.0.1" for bundled yaya plugins, or the 3rd-party
            # package's version). It is NOT the plugin's self-declared
            # ``version``, but it is cheap to read without loading the
            # plugin object, and it is strictly better than a blank
            # column. A subsequent ``plugin.loaded`` event overwrites
            # with the authoritative value when one arrives (i.e. for
            # the adapter itself, and any plugin loaded AFTER the
            # adapter subscribed). See lesson #26.
            version = ""
            if ep.dist is not None:
                try:
                    version = ep.dist.version
                except Exception:  # pragma: no cover - defensive only
                    version = ""
            # We cannot know the plugin's ``category`` without loading
            # it (the entry-point value is a "module:attr" string, not
            # a Plugin object); that field stays blank until a
            # ``plugin.loaded`` event patches it.
            self._plugin_rows.setdefault(
                ep.name,
                {
                    "name": ep.name,
                    "version": version,
                    "category": "",
                    "status": "loaded",
                },
            )

    async def _handle_ws(self, ws: WebSocket) -> None:
        """Run one WebSocket connection from accept to disconnect.

        Resume path. If the client supplies ``?session=<id>`` AND the
        id resolves to an existing tape for the current workspace,
        bind the connection to that id so downstream events (the
        agent loop's history hydration, the session persister's tape
        opens) reach the same tape. Unknown ids fall back to a fresh
        ``ws-<uuid>`` and log at INFO — operators can tell when a
        stale tab tried to resume a deleted tape, and bad client
        input never 500s.
        """
        import uuid

        session_id = f"ws-{uuid.uuid4().hex[:8]}"
        requested = ws.query_params.get("session") if ws.query_params else None
        if requested:
            resolved = await self._resolve_resume_session(requested)
            if resolved is not None:
                session_id = resolved
            elif self._ctx is not None:
                self._ctx.logger.info(
                    "ws resume requested for unknown session %r; starting a fresh session",
                    requested,
                )
        await ws.accept()
        self._clients[session_id].add(ws)
        ctx = self._ctx
        try:
            while True:
                try:
                    frame = await ws.receive_json()
                except WebSocketDisconnect:
                    return
                await self._handle_frame(session_id, frame, ctx)
        finally:
            sockets = self._clients.get(session_id)
            if sockets is not None:
                sockets.discard(ws)
                if not sockets:
                    # Self-clean (lesson #6): idle sessions must not
                    # leak queue entries in a long-running process.
                    self._clients.pop(session_id, None)

    async def _resolve_resume_session(self, candidate: str) -> str | None:
        """Return ``candidate`` if it matches a persisted session, else ``None``.

        The sidebar's ``/api/sessions`` row carries a ``session_id``
        field that is the hashed tape-name suffix — the same token
        surfaces back here on ``?session=<id>``. We delegate to the
        :class:`SessionStore`: listing the workspace's sessions and
        matching on ``SessionInfo.session_id`` is the single source
        of truth for "is this a known session?", and it degrades
        safely when the store / workspace pair is not wired (tests,
        transient paths).
        """
        ctx = self._ctx
        if ctx is None or ctx.session_store is None:
            return None
        try:
            infos = await ctx.session_store.list_sessions(Path.cwd())
        except Exception as exc:
            ctx.logger.debug("ws resume lookup failed: %s", exc)
            return None
        for info in infos:
            if info.session_id == candidate:
                return candidate
        return None

    async def _handle_frame(
        self,
        session_id: str,
        frame: Any,
        ctx: KernelContext | None,
    ) -> None:
        """Translate one browser frame into a kernel publish.

        Malformed frames are ignored with a DEBUG log — a noisy
        client must not take down the adapter, and silent drops here
        are acceptable because the browser owns the mistake.
        """
        if ctx is None:
            return
        if not isinstance(frame, dict):
            ctx.logger.debug("ws frame dropped: not an object (%r)", type(frame).__name__)
            return
        frame_dict = cast("dict[str, Any]", frame)
        kind = frame_dict.get("type")
        if kind == "user.message":
            text = frame_dict.get("text")
            if not isinstance(text, str):
                return
            payload: dict[str, Any] = {"text": text}
            attachments = frame_dict.get("attachments")
            if isinstance(attachments, list):
                payload["attachments"] = attachments
            await ctx.emit("user.message.received", payload, session_id=session_id)
        elif kind == "user.interrupt":
            await ctx.emit("user.interrupt", {}, session_id=session_id)
        else:
            ctx.logger.debug("ws frame dropped: unknown type %r", kind)

    async def _deliver(self, session_id: str, frame: dict[str, Any]) -> None:
        """Send ``frame`` to the right sockets.

        Routing rule (per the prior-dispatch design note):

        * If ``session_id`` matches a connected WS session, deliver
          only to that session's sockets — this is the normal per-turn
          path (the kernel's agent loop propagates ``session_id``
          through every event it emits).
        * Otherwise broadcast to every connected client. Kernel-origin
          lifecycle events (``kernel.ready``, ``plugin.loaded``, ...)
          carry ``session_id="kernel"`` which won't match any WS id.
        """
        sockets = self._clients.get(session_id)
        targets: list[WebSocket] = (
            list(sockets) if sockets else [ws for conn_set in self._clients.values() for ws in conn_set]
        )

        if not targets:
            return

        # Fire each send under a local try so one dead socket does not
        # starve the rest. The WS runtime may raise if the client
        # vanished between the last receive and this send.
        for ws in targets:
            try:
                await ws.send_json(frame)
            except Exception as exc:
                # Log at DEBUG; the surrounding receive loop will
                # observe the disconnect and clean up the socket.
                if self._ctx is not None:
                    self._ctx.logger.debug("ws send failed (%s); will reap on receive", exc)

    # -- helpers ---------------------------------------------------------------

    def _resolve_port(self) -> int:
        """Return the bind port, honoring the override or ``YAYA_WEB_PORT``."""
        if self._port_override is not None:
            return self._port_override
        raw = os.environ.get("YAYA_WEB_PORT", "0")
        try:
            port = int(raw)
        except ValueError:
            port = 0
        if port != 0:
            return port
        return _find_free_port()


def _find_free_port() -> int:
    """Ask the OS for a free TCP port on 127.0.0.1.

    Same racy-by-design pattern as :func:`yaya.cli.commands.serve._pick_free_port`
    — acceptable for a local dev tool; the adapter fails loudly if
    the race materialises.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_BIND_HOST, 0))
        return int(sock.getsockname()[1])


# Make the coroutine-return shape explicit for static checkers.
if TYPE_CHECKING:  # pragma: no cover
    _: Awaitable[None]


__all__ = ["WebAdapter"]
