"""MCP bridge plugin: spawn configured MCP servers, register their tools.

The plugin owns one :class:`~yaya.plugins.mcp_bridge.client.MCPClient`
per configured server. ``on_load`` reads
``ctx.config["servers"]`` (populated from
``[mcp_bridge.servers.<name>]`` in the user's TOML), spawns each
enabled server with up to three retries (exponential backoff: 0.5s,
1.0s, 2.0s), and registers every tool the server advertises through
the kernel's tool registry. ``on_unload`` tears every client down
following the lesson #31 cancel pattern.

Per-server failures (boot crash, retries exhausted) emit a
plugin-private ``x.mcp.server.error`` event so adapters that subscribe
to the extension namespace see the surface; per-server success emits
``x.mcp.server.ready`` carrying the discovered tool list. Neither
event is part of the closed public catalog (see GOAL.md principle #3).

Per the hard rules in issue #31, every MCP-derived tool defaults to
``requires_approval=True`` — MCP servers are external code surfaces
that yaya does not trust by default.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, ClassVar

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, HealthReport, KernelContext
from yaya.kernel.tool import Tool, register_tool, unregister_tool
from yaya.plugins.mcp_bridge.client import (
    MCPClient,
    MCPClientError,
    MCPToolDescriptor,
)
from yaya.plugins.mcp_bridge.config import MCPServerConfig, parse_mcp_config
from yaya.plugins.mcp_bridge.tool_factory import (
    build_mcp_tool_class,
    mcp_tool_qualified_name,
)

_NAME = "mcp-bridge"
_VERSION = "0.1.0"
_DEFAULT_REQUIRES_APPROVAL = True
_RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0, 2.0)
"""Boot-retry backoff schedule. Three attempts → three entries."""

# Plugin-private extension event kinds. Routed through the bus per
# GOAL.md principle #3 ``x.<plugin>.<kind>`` namespace.
EVENT_SERVER_READY = "x.mcp.server.ready"
EVENT_SERVER_ERROR = "x.mcp.server.error"


class MCPBridge:
    """Bundled MCP-bridge plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.TOOL`.
        retry_delays_s: Retry backoff schedule for boot failures.
            Override in tests via the constructor knob.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(
        self,
        *,
        retry_delays_s: tuple[float, ...] = _RETRY_DELAYS_S,
        client_factory: Any = None,
    ) -> None:
        """Record wiring; no I/O until :meth:`on_load`.

        Args:
            retry_delays_s: Tuple of seconds to sleep between boot
                attempts. Length determines max attempts.
            client_factory: Test-only callable used in lieu of
                :class:`MCPClient`. Receives ``(command, args, env,
                logger)`` and returns an object honouring the
                :class:`MCPClient` surface (``start``, ``call_tool``,
                ``close``). Production code passes ``None`` so the
                real client is used.
        """
        self.retry_delays_s = retry_delays_s
        self._client_factory = client_factory or _default_client_factory
        # name → (client, tool classes registered for this server). Kept
        # so ``on_unload`` can close clients deterministically and
        # diagnostics can map tools back to their owning server.
        self._servers: dict[str, _ServerRecord] = {}

    def subscriptions(self) -> list[str]:
        """The bridge is purely a tool *provider* — no event subscriptions.

        Inbound ``tool.call.request`` events for MCP tools flow through
        the kernel's v1 dispatcher (see
        :func:`yaya.kernel.tool.dispatch`), which looks the tool up in
        the registry populated during :meth:`on_load`. The bridge
        therefore subscribes to nothing on the bus.
        """
        return []

    async def on_load(self, ctx: KernelContext) -> None:
        """Parse config, spawn each enabled server, register its tools."""
        configs, errors = parse_mcp_config(ctx.config)
        for name, message in errors:
            ctx.logger.warning("mcp-bridge: bad config for %r: %s", name, message)
            await _emit_server_error(ctx, name, "config_invalid", message)

        if not configs:
            ctx.logger.debug("mcp-bridge: no servers configured")
            return

        for cfg in configs:
            if not cfg.enabled:
                ctx.logger.info("mcp-bridge: server %r disabled by config", cfg.name)
                continue
            await self._boot_server(cfg, ctx)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """No-op — bridge does not subscribe to anything (see :meth:`subscriptions`)."""

    async def on_unload(self, ctx: KernelContext) -> None:
        """Unregister every tool and close every live client. Idempotent.

        Tools are unregistered BEFORE clients are closed so a concurrent
        dispatch attempt cannot race with a half-closed client — the
        registry lookup returns ``None`` (→ ``tool.error(kind=
        "not_found")``) rather than hitting the closed transport.
        Subprocess teardown follows lesson #31 inside
        :meth:`MCPClient.close`.
        """
        records = list(self._servers.values())
        self._servers.clear()
        for record in records:
            for tool_name in record.tool_names:
                unregister_tool(tool_name)
            try:
                await record.client.close()
            except Exception:
                ctx.logger.exception("mcp-bridge: error closing %r", record.config.name)

    async def health_check(self, ctx: KernelContext) -> HealthReport:
        """Report live MCP-server count from in-memory state.

        Does NOT open or ping any server — a health check must be
        cheap, and an MCP stdio handshake would race the plugin's
        own live clients. The live :attr:`_servers` map is the
        ground truth after :meth:`on_load` has run.

        * Zero live records → ``ok`` ("no servers configured").
        * N live records → ``ok`` ("N server(s) ready").

        A boot failure emits ``x.mcp.server.error`` (see
        :meth:`_boot_server`) but does not leave a stale entry in
        :attr:`_servers`, so a partially-failed boot surfaces as
        the reduced live count rather than a special case here.
        """
        del ctx  # inspection is fully self-contained in _servers.
        live = len(self._servers)
        if live == 0:
            return HealthReport(status="ok", summary="no servers configured")
        return HealthReport(status="ok", summary=f"{live} server(s) ready")

    # -- internals ---------------------------------------------------------

    async def _boot_server(self, cfg: MCPServerConfig, ctx: KernelContext) -> None:
        """Spawn ``cfg`` with retries; register tools on success.

        Each attempt builds a fresh client — a partially-initialized
        client from a failed attempt is closed before retrying so we
        never leak a process across the backoff. After
        ``len(self.retry_delays_s)`` attempts the server is given up
        on; the bridge keeps running and emits
        ``x.mcp.server.error`` so operators see the give-up.
        """
        last_error: BaseException | None = None
        for attempt, delay in enumerate(self.retry_delays_s, start=1):
            client = self._client_factory(
                cfg.command,
                cfg.args,
                env=cfg.env,
                logger=ctx.logger,
            )
            try:
                tools = await client.start()
            except (TimeoutError, MCPClientError, OSError) as exc:
                last_error = exc
                ctx.logger.warning(
                    "mcp-bridge: %r boot attempt %d/%d failed: %s",
                    cfg.name,
                    attempt,
                    len(self.retry_delays_s),
                    exc,
                )
                # Best-effort cleanup of the failed attempt before sleeping.
                # The retry path already logged the boot failure above; close()
                # noise on a half-built client adds nothing and would crowd logs.
                with contextlib.suppress(Exception):
                    await client.close()
                if attempt < len(self.retry_delays_s):
                    await asyncio.sleep(delay)
                continue

            await self._register_server(cfg, client, tools, ctx)
            return

        # All attempts exhausted.
        message = str(last_error) if last_error is not None else "unknown boot failure"
        ctx.logger.error(
            "mcp-bridge: %r failed to boot after %d attempts; giving up",
            cfg.name,
            len(self.retry_delays_s),
        )
        await _emit_server_error(ctx, cfg.name, "boot_failed", message)

    async def _register_server(
        self,
        cfg: MCPServerConfig,
        client: MCPClient,
        tools: list[MCPToolDescriptor],
        ctx: KernelContext,
    ) -> None:
        """Wire ``client``'s tools into the kernel registry, record state, emit ready."""
        requires_approval = cfg.requires_approval if cfg.requires_approval is not None else _DEFAULT_REQUIRES_APPROVAL
        registered: list[str] = []
        for descriptor in tools:
            tool_cls = build_mcp_tool_class(
                cfg.name,
                descriptor,
                client,
                requires_approval=requires_approval,
                call_timeout_s=cfg.call_timeout_s,
            )
            try:
                register_tool(tool_cls)
            except ValueError as exc:
                # Name collision or missing ClassVar — surface but do
                # not abort the rest of the server's tools.
                ctx.logger.warning(
                    "mcp-bridge: failed to register %r: %s",
                    tool_cls.name,
                    exc,
                )
                continue
            registered.append(tool_cls.name)

        self._servers[cfg.name] = _ServerRecord(
            config=cfg,
            client=client,
            tool_names=tuple(registered),
        )
        ctx.logger.info(
            "mcp-bridge: %r ready with %d tool(s): %s",
            cfg.name,
            len(registered),
            ", ".join(registered) or "<none>",
        )
        await ctx.emit(
            EVENT_SERVER_READY,
            {
                "server": cfg.name,
                "tools": [
                    {
                        "name": mcp_tool_qualified_name(cfg.name, descriptor.name),
                        "mcp_name": descriptor.name,
                        "description": descriptor.description,
                    }
                    for descriptor in tools
                ],
            },
            session_id=_BRIDGE_SESSION,
        )


# Single session id used for plugin-private bridge events. Bus serializes
# delivery per session, so a stable id keeps these events well-ordered
# without polluting any conversation's session_id space. The
# ``_bridge:`` prefix marks this as a private routing channel (distinct
# from the plugin *name* ``mcp-bridge`` used in the registry) so future
# subsystems can key on session id for plugin identity without
# collisions.
_BRIDGE_SESSION = "_bridge:mcp-bridge"


def _default_client_factory(
    command: str,
    args: list[str],
    *,
    env: dict[str, str],
    logger: Any,
) -> MCPClient:
    """Production :class:`MCPClient` factory; tests inject their own."""
    return MCPClient(command, args, env=env, logger=logger)


async def _emit_server_error(
    ctx: KernelContext,
    server: str,
    kind: str,
    message: str,
) -> None:
    """Publish ``x.mcp.server.error`` on behalf of the bridge.

    Plugin-private extension — never raises out of the boot path; an
    emit failure is logged and swallowed so a misconfigured one server
    cannot silently abort the rest of the bridge.
    """
    try:
        await ctx.emit(
            EVENT_SERVER_ERROR,
            {"server": server, "kind": kind, "message": message},
            session_id=_BRIDGE_SESSION,
        )
    except Exception:
        ctx.logger.exception("mcp-bridge: failed to emit %s for %r", EVENT_SERVER_ERROR, server)


class _ServerRecord:
    """Per-server in-memory state.

    Attributes:
        config: Validated config that produced this record.
        client: Live :class:`MCPClient`. Owned by the bridge for the
            lifetime of the plugin.
        tool_names: yaya-side qualified names registered with the
            kernel for this server. Held for diagnostics; the registry
            is the source of truth.
    """

    __slots__ = ("client", "config", "tool_names")

    def __init__(
        self,
        *,
        config: MCPServerConfig,
        client: MCPClient,
        tool_names: tuple[str, ...],
    ) -> None:
        self.config = config
        self.client = client
        self.tool_names = tool_names


# Re-exports for tests that need to introspect a generated tool's source.
def _registered_tool_classes(tool_names: list[str]) -> list[type[Tool]]:
    """Return registered :class:`Tool` subclasses for ``tool_names``.

    Helper kept for tests that want to assert the dynamic class shape
    without re-walking the kernel registry by name. Defined at module
    level (rather than as a static method) so import surface stays
    unchanged across refactors.
    """
    from yaya.kernel.tool import get_tool

    out: list[type[Tool]] = []
    for name in tool_names:
        cls = get_tool(name)
        if cls is not None:
            out.append(cls)
    return out


__all__ = [
    "EVENT_SERVER_ERROR",
    "EVENT_SERVER_READY",
    "MCPBridge",
]
