"""Tests for the MCP bridge plugin.

Each test wires a fresh kernel tool registry so dynamically-built
:class:`Tool` subclasses from earlier scenarios do not leak across
tests. Integration cases spawn the pure-Python fake server under
``_fake_server.py`` — no real network, no `npx`/`uvx` dependency.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.tool import (
    _clear_tool_registry,
    get_tool,
    install_dispatcher,
    register_tool,
    registered_tools,
)
from yaya.plugins.mcp_bridge.client import MCPClientError, MCPToolDescriptor
from yaya.plugins.mcp_bridge.plugin import (
    EVENT_SERVER_ERROR,
    EVENT_SERVER_READY,
    MCPBridge,
)
from yaya.plugins.mcp_bridge.tool_factory import (
    build_mcp_tool_class,
    mcp_tool_qualified_name,
)

_FAKE_SERVER = Path(__file__).resolve().parent / "_fake_server.py"


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Snap the kernel tool registry per-test so dynamic classes do not leak."""
    _clear_tool_registry()
    yield
    _clear_tool_registry()


def _make_ctx(tmp_path: Path, bus: EventBus, *, config: dict[str, Any] | None = None) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.mcp-bridge"),
        config=config or {},
        state_dir=tmp_path,
        plugin_name="mcp-bridge",
    )


class _FakeClient:
    """Test stand-in for :class:`MCPClient` honouring the surface used by the plugin."""

    def __init__(
        self,
        *,
        descriptors: list[MCPToolDescriptor] | None = None,
        start_error: BaseException | None = None,
        call_handler: Any = None,
    ) -> None:
        self._descriptors = descriptors or []
        self._start_error = start_error
        self._call_handler = call_handler
        self.start_calls = 0
        self.close_calls = 0
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> list[MCPToolDescriptor]:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        return list(self._descriptors)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        self.tool_calls.append((tool_name, arguments))
        if self._call_handler is not None:
            return await self._call_handler(tool_name, arguments)
        return {"content": [{"type": "text", "text": tool_name}], "isError": False}

    async def close(self) -> None:
        self.close_calls += 1


def _factory_for(client: _FakeClient) -> Any:
    def _make(command: str, args: list[str], *, env: dict[str, str], logger: Any) -> _FakeClient:
        return client

    return _make


def _factory_sequence(clients: list[_FakeClient]) -> Any:
    """Hand out fresh ``_FakeClient`` instances in order — one per attempt/server."""
    iterator = iter(clients)

    def _make(command: str, args: list[str], *, env: dict[str, str], logger: Any) -> _FakeClient:
        return next(iterator)

    return _make


@pytest.fixture
async def captured_bus() -> AsyncIterator[tuple[EventBus, list[Event]]]:
    bus = EventBus()
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe(EVENT_SERVER_READY, _observer, source="observer-ready")
    bus.subscribe(EVENT_SERVER_ERROR, _observer, source="observer-error")
    yield bus, captured
    await bus.close()


# ---------------------------------------------------------------------------
# AC scenarios.
# ---------------------------------------------------------------------------


async def test_discovers_and_registers_tools(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """Spawning the real fake_server lands `mcp_local_echo` in the registry."""
    bus, captured = captured_bus
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={
            "servers": {
                "local": {
                    "command": sys.executable,
                    "args": [str(_FAKE_SERVER)],
                }
            }
        },
    )
    bridge = MCPBridge(retry_delays_s=(0.0,))
    await bridge.on_load(ctx)
    try:
        assert get_tool("mcp_local_echo") is not None, registered_tools()
        assert get_tool("mcp_local_slow") is not None
        ready = [ev for ev in captured if ev.kind == EVENT_SERVER_READY]
        assert len(ready) == 1
        assert ready[0].payload["server"] == "local"
        names = {entry["name"] for entry in ready[0].payload["tools"]}
        assert {"mcp_local_echo", "mcp_local_slow"}.issubset(names)
    finally:
        await bridge.on_unload(ctx)


async def test_derived_tool_requires_approval_by_default(tmp_path: Path) -> None:
    """The factory honours the bridge default: ``requires_approval=True``."""
    descriptor = MCPToolDescriptor(
        name="echo",
        description="echo back",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    )
    client = _FakeClient(descriptors=[descriptor])
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=True,
        call_timeout_s=5.0,
    )
    assert tool_cls.requires_approval is True
    assert tool_cls.name == "mcp_local_echo"


async def test_tool_call_forwards_and_wraps_ok(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """Dispatch a tool.call.request and observe a ToolOk envelope on the bus."""
    bus, _captured = captured_bus
    install_dispatcher(bus)
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={
            "servers": {
                "local": {
                    "command": sys.executable,
                    "args": [str(_FAKE_SERVER)],
                    "requires_approval": False,
                }
            }
        },
    )
    bridge = MCPBridge(retry_delays_s=(0.0,))
    await bridge.on_load(ctx)
    try:
        results: list[Event] = []

        async def _observer(ev: Event) -> None:
            results.append(ev)

        bus.subscribe("tool.call.result", _observer, source="observer")

        from yaya.kernel.events import new_event

        request = new_event(
            "tool.call.request",
            {
                "schema_version": "v1",
                "id": "call-1",
                "name": "mcp_local_echo",
                "args": {"msg": "hello world"},
            },
            session_id="sess-1",
            source="kernel",
        )
        await bus.publish(request)

        assert len(results) == 1, results
        envelope = results[0].payload["envelope"]
        assert envelope["ok"] is True
        assert "hello world" in envelope["brief"]
    finally:
        await bridge.on_unload(ctx)


async def test_boot_failure_emits_server_error_after_retries(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """All retries fail → exactly one x.mcp.server.error with kind=boot_failed."""
    bus, captured = captured_bus
    failing_clients = [_FakeClient(start_error=MCPClientError("boom")) for _ in range(2)]
    bridge = MCPBridge(
        retry_delays_s=(0.0, 0.0),
        client_factory=_factory_sequence(failing_clients),
    )
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={"servers": {"local": {"command": "/no/such/bin"}}},
    )
    await bridge.on_load(ctx)

    assert get_tool("mcp_local_echo") is None
    errors = [ev for ev in captured if ev.kind == EVENT_SERVER_ERROR]
    assert len(errors) == 1, errors
    assert errors[0].payload["kind"] == "boot_failed"
    assert errors[0].payload["server"] == "local"
    # Each retry should close its half-built client (lesson #31 hygiene).
    assert all(client.close_calls >= 1 for client in failing_clients)


async def test_bad_config_emits_error_and_does_not_taint_others(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """A malformed server entry surfaces as x.mcp.server.error; siblings still load."""
    bus, captured = captured_bus
    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )
    good_client = _FakeClient(descriptors=[descriptor])
    bridge = MCPBridge(
        retry_delays_s=(0.0,),
        client_factory=_factory_for(good_client),
    )
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={
            "servers": {
                "broken": {"command": ""},  # invalid: empty command.
                "good": {"command": "irrelevant"},
            }
        },
    )
    await bridge.on_load(ctx)
    try:
        errors = [ev for ev in captured if ev.kind == EVENT_SERVER_ERROR]
        assert any(ev.payload["server"] == "broken" for ev in errors)
        assert get_tool("mcp_good_echo") is not None
    finally:
        await bridge.on_unload(ctx)


async def test_unload_closes_every_client(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """on_unload awaits close() on every spawned client."""
    bus, _captured = captured_bus
    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )
    clients = [_FakeClient(descriptors=[descriptor]) for _ in range(2)]

    sequence = iter(clients)

    def _factory(command: str, args: list[str], *, env: dict[str, str], logger: Any) -> _FakeClient:
        return next(sequence)

    bridge = MCPBridge(retry_delays_s=(0.0,), client_factory=_factory)
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={
            "servers": {
                "alpha": {"command": "x"},
                "beta": {"command": "y"},
            }
        },
    )
    await bridge.on_load(ctx)
    await bridge.on_unload(ctx)

    assert all(client.close_calls == 1 for client in clients)


# ---------------------------------------------------------------------------
# Unit tests for inner pieces — exercise edge cases the AC list does not cover.
# ---------------------------------------------------------------------------


async def test_qualified_name_sanitizes_punctuation() -> None:
    """`-`, `.`, `:` collapse to underscores so the LLM surface accepts the name."""
    assert mcp_tool_qualified_name("my server", "weird.name:1") == "mcp_my_server_weird_name_1"


async def test_tool_call_translates_timeout_to_tool_error(tmp_path: Path) -> None:
    """A client timeout surfaces as ``ToolError(kind="timeout")`` per lesson #29."""
    from yaya.kernel.tool import ToolError
    from yaya.plugins.mcp_bridge.client import MCPTimeoutError

    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )

    async def _raise_timeout(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise MCPTimeoutError("boom")

    client = _FakeClient(descriptors=[descriptor], call_handler=_raise_timeout)
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    register_tool(tool_cls)
    instance = tool_cls.model_validate({"msg": "hi"})
    bus = EventBus()
    ctx = _make_ctx(tmp_path, bus)
    try:
        result = await instance.run(ctx)
        assert isinstance(result, ToolError)
        assert result.kind == "timeout"
    finally:
        await bus.close()


async def test_disabled_server_is_not_spawned(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """`enabled = false` prevents subprocess spawn and registry inserts."""
    bus, captured = captured_bus
    client = _FakeClient(descriptors=[])
    bridge = MCPBridge(retry_delays_s=(0.0,), client_factory=_factory_for(client))
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={
            "servers": {
                "off": {"command": "x", "enabled": False},
            }
        },
    )
    await bridge.on_load(ctx)
    assert client.start_calls == 0
    assert not [ev for ev in captured if ev.kind == EVENT_SERVER_READY]


async def test_client_close_terminates_then_kills() -> None:
    """``MCPClient.close`` falls back to kill when terminate is ignored."""
    from yaya.plugins.mcp_bridge.client import MCPClient

    # Spawn a child that swallows SIGTERM so terminate() does NOT exit it.
    code = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=0.2)
    # Short-circuit start(): we only want close() to exercise the cancel path.
    client._proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Wait for the child to print "ready" so we know the SIGTERM handler is installed.
    assert client._proc.stdout is not None
    await client._proc.stdout.readline()

    await client.close()
    assert client._proc is not None
    assert client._proc.returncode is not None
