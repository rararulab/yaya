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


async def test_on_unload_unregisters_dynamic_tools(
    tmp_path: Path,
    captured_bus: tuple[EventBus, list[Event]],
) -> None:
    """on_unload drops every dynamically-registered MCP tool from the kernel registry (#90).

    Without the unregister hook a bridge hot-reload would leave stale
    Tool subclasses pointing at a closed client in the registry —
    correctness-safe (dispatch would return ToolError) but ugly UX.
    """
    bus, _captured = captured_bus
    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )
    client = _FakeClient(descriptors=[descriptor])
    bridge = MCPBridge(
        retry_delays_s=(0.0,),
        client_factory=_factory_for(client),
    )
    ctx = _make_ctx(
        tmp_path,
        bus,
        config={"servers": {"alpha": {"command": "x"}}},
    )
    await bridge.on_load(ctx)
    assert get_tool("mcp_alpha_echo") is not None

    await bridge.on_unload(ctx)
    # Tool row is gone — hot-reload leaves no stale registration.
    assert get_tool("mcp_alpha_echo") is None
    assert "mcp_alpha_echo" not in registered_tools()


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


# ---------------------------------------------------------------------------
# Additional client-level coverage.
# ---------------------------------------------------------------------------


async def test_client_close_cancels_reader_tasks_and_fails_pending() -> None:
    """close() cancels reader/stderr tasks and fails outstanding futures."""
    from yaya.plugins.mcp_bridge.client import (
        MCPClient,
        MCPServerCrashedError,
        _PendingRequest,
    )

    # A long-running child so close() must terminate it + cancel tasks.
    code = "import sys, time\nsys.stdout.write('ready\\n'); sys.stdout.flush()\ntime.sleep(60)\n"
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    client._proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert client._proc.stdout is not None
    await client._proc.stdout.readline()

    # Spin the real reader + stderr loops so close() must cancel them.
    client._reader_task = asyncio.create_task(client._read_loop(), name="reader")
    client._stderr_task = asyncio.create_task(client._stderr_loop(), name="stderr")

    # Inject a pending future so close() takes the "fail pending" branch.
    loop = asyncio.get_running_loop()
    pending_future: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[42] = _PendingRequest(future=pending_future, method="tools/call")

    await client.close()
    # Second close() is a no-op (idempotent short-circuit at the top).
    await client.close()
    assert client._reader_task is None
    assert client._stderr_task is None
    assert client._pending == {}
    assert pending_future.done()
    with pytest.raises(MCPServerCrashedError):
        pending_future.result()


async def test_client_request_after_close_raises() -> None:
    """``_request`` on a closed client surfaces ``MCPServerCrashedError``."""
    from yaya.plugins.mcp_bridge.client import MCPClient, MCPServerCrashedError

    client = MCPClient("nope", [])
    client._closed = True
    with pytest.raises(MCPServerCrashedError):
        await client._request("tools/list", {})
    with pytest.raises(MCPServerCrashedError):
        await client._notify("notifications/initialized", {})


async def test_read_loop_skips_garbage_and_resolves_pending() -> None:
    """Malformed lines are skipped; subsequent valid response completes its pending future."""
    from yaya.plugins.mcp_bridge.client import MCPClient, _PendingRequest

    # Echoes garbage, then a valid JSON-RPC response, then exits.
    code = (
        "import sys\n"
        "sys.stdout.write('not json\\n')\n"
        "sys.stdout.write('\\n')\n"  # empty line branch
        "sys.stdout.write('[\"not a dict\"]\\n')\n"  # non-dict branch
        'sys.stdout.write(\'{"jsonrpc":"2.0","id":"not-int","result":{}}\\n\')\n'
        'sys.stdout.write(\'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\\n\')\n'
        "sys.stdout.flush()\n"
    )
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    client._proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[1] = _PendingRequest(future=fut, method="tools/call")

    reader = asyncio.create_task(client._read_loop())
    try:
        result = await asyncio.wait_for(fut, timeout=2.0)
        assert result == {"ok": True}
    finally:
        # Let the reader observe EOF (child already exited) and finish.
        await asyncio.wait_for(reader, timeout=2.0)
        await client.close()


async def test_read_loop_on_eof_fails_pending_futures() -> None:
    """When the child closes stdout, pending futures resolve with ``MCPServerCrashedError``."""
    from yaya.plugins.mcp_bridge.client import (
        MCPClient,
        MCPServerCrashedError,
        _PendingRequest,
    )

    # Child exits immediately → EOF right away.
    code = "import sys; sys.stdout.flush()\n"
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    client._proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[7] = _PendingRequest(future=fut, method="tools/call")

    reader = asyncio.create_task(client._read_loop())
    try:
        with pytest.raises(MCPServerCrashedError):
            await asyncio.wait_for(fut, timeout=2.0)
    finally:
        await asyncio.wait_for(reader, timeout=2.0)
        await client.close()


async def test_dispatch_message_translates_error_payload_to_protocol_error() -> None:
    """JSON-RPC ``error`` payloads surface as :class:`MCPProtocolError`."""
    from yaya.plugins.mcp_bridge.client import (
        MCPClient,
        MCPProtocolError,
        _PendingRequest,
    )

    client = MCPClient("nope", [])
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[1] = _PendingRequest(future=fut, method="initialize")
    client._dispatch_message({
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32600, "message": "bad request"},
    })
    with pytest.raises(MCPProtocolError, match="bad request"):
        fut.result()

    # Non-dict ``error`` payload also stringifies cleanly.
    fut2: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[2] = _PendingRequest(future=fut2, method="tools/call")
    client._dispatch_message({"jsonrpc": "2.0", "id": 2, "error": "boom-string"})
    with pytest.raises(MCPProtocolError, match="boom-string"):
        fut2.result()

    # Response with non-object ``result`` surfaces as protocol error too.
    fut3: asyncio.Future[dict[str, Any]] = loop.create_future()
    client._pending[3] = _PendingRequest(future=fut3, method="tools/list")
    client._dispatch_message({"jsonrpc": "2.0", "id": 3, "result": "not-an-object"})
    with pytest.raises(MCPProtocolError, match="result missing"):
        fut3.result()

    # Notification (no id) and unknown id are both quietly ignored.
    client._dispatch_message({"jsonrpc": "2.0", "method": "notifications/foo"})
    client._dispatch_message({"jsonrpc": "2.0", "id": 999, "result": {}})


async def test_call_tool_timeout_raises_and_cleans_pending() -> None:
    """A ``timeout_s`` breach surfaces as :class:`MCPTimeoutError` and clears _pending."""
    from yaya.plugins.mcp_bridge.client import MCPClient, MCPTimeoutError

    # Slow fake server: only replies to initialize; "slow" sleeps.
    client = MCPClient(sys.executable, [str(_FAKE_SERVER)], term_grace_s=1.0)
    try:
        await client.start()
        with pytest.raises(MCPTimeoutError):
            await client.call_tool("slow", {"seconds": 5}, timeout_s=0.05)
        # Pending entry for the timed-out call was cleaned by the ``finally`` in _request.
        assert client._pending == {}
    finally:
        await client.close()


async def test_start_initialize_error_raises_protocol_error() -> None:
    """A server that returns an error on ``initialize`` surfaces MCPProtocolError."""
    from yaya.plugins.mcp_bridge.client import MCPClient, MCPProtocolError

    # Tiny inline server: always reply with an error payload.
    code = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if msg.get('method') in ('notifications/initialized',):\n"
        "        continue\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':msg.get('id'),"
        "'error':{'code':-1,'message':'nope'}}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    try:
        with pytest.raises(MCPProtocolError):
            await client.start()
    finally:
        await client.close()


async def test_list_tools_rejects_non_list_payload() -> None:
    """``tools/list`` returning a non-list surfaces as :class:`MCPProtocolError`."""
    from yaya.plugins.mcp_bridge.client import MCPClient, MCPProtocolError

    code = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        "    mid = msg.get('id')\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,'result':{"
        "'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':"
        "{'name':'x','version':'0'}}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "    elif method == 'notifications/initialized':\n"
        "        pass\n"
        "    elif method == 'tools/list':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'tools':'not-a-list'}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
    )
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    try:
        with pytest.raises(MCPProtocolError):
            await client.start()
    finally:
        await client.close()


async def test_list_tools_skips_malformed_entries() -> None:
    """Non-dict / nameless tool entries are silently skipped."""
    from yaya.plugins.mcp_bridge.client import MCPClient

    code = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        "    mid = msg.get('id')\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,'result':{"
        "'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':"
        "{'name':'x','version':'0'}}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "    elif method == 'notifications/initialized':\n"
        "        pass\n"
        "    elif method == 'tools/list':\n"
        "        tools = ['not-a-dict', {'name': ''}, {'name': 123}, "
        "{'name':'good','description':None,'inputSchema':'not-a-dict'}]\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'tools':tools}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
    )
    client = MCPClient(sys.executable, ["-c", code], term_grace_s=1.0)
    try:
        tools = await client.start()
        assert [t.name for t in tools] == ["good"]
        assert tools[0].input_schema == {}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# config.py validation coverage.
# ---------------------------------------------------------------------------


def test_config_rejects_non_table_entry() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": "not-a-table"}})
    assert good == []
    assert errors and "expected a table" in errors[0][1]


def test_config_rejects_missing_command() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": 0}}})
    assert good == []
    assert any("command" in msg for _, msg in errors)


def test_config_rejects_non_list_args() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "args": "not-a-list"}}})
    assert good == []
    assert any("'args'" in msg for _, msg in errors)


def test_config_rejects_non_string_arg_element() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "args": [1]}}})
    assert good == []
    assert any("args[0]" in msg for _, msg in errors)


def test_config_rejects_non_dict_env() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "env": "nope"}}})
    assert good == []
    assert any("'env'" in msg for _, msg in errors)


def test_config_rejects_non_string_env_values() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "env": {"K": 1}}}})
    assert good == []
    assert any("string" in msg for _, msg in errors)


def test_config_rejects_non_bool_enabled() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "enabled": "yes"}}})
    assert good == []
    assert any("'enabled'" in msg for _, msg in errors)


def test_config_rejects_non_bool_approval() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "requires_approval": "sure"}}})
    assert good == []
    assert any("requires_approval" in msg for _, msg in errors)


def test_config_rejects_non_positive_timeout() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "call_timeout_s": 0}}})
    assert good == []
    assert any("call_timeout_s" in msg for _, msg in errors)


def test_config_rejects_bool_as_timeout() -> None:
    """``isinstance(True, int)`` is True in Python — must not leak into timeouts."""
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"bad": {"command": "x", "call_timeout_s": True}}})
    assert good == []
    assert any("call_timeout_s" in msg for _, msg in errors)


def test_config_non_dict_input_returns_empty() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    assert parse_mcp_config("nope") == ([], [])
    assert parse_mcp_config({"servers": None}) == ([], [])


def test_config_servers_not_table_is_reported() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": [1, 2, 3]})
    assert good == []
    assert any("servers" in msg for _, msg in errors)


def test_config_rejects_empty_server_name() -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    good, errors = parse_mcp_config({"servers": {"": {"command": "x"}}})
    assert good == []
    assert any("non-empty string" in msg for _, msg in errors)


def test_config_env_expansion_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    monkeypatch.setenv("YAYA_TEST_TOKEN", "s3cret")
    good, errors = parse_mcp_config({
        "servers": {
            "x": {
                "command": "/bin/${YAYA_TEST_TOKEN}",
                "args": ["--key=$YAYA_TEST_TOKEN"],
                "env": {"K": "v-$YAYA_TEST_TOKEN"},
            }
        }
    })
    assert errors == []
    assert len(good) == 1
    cfg = good[0]
    assert cfg.command == "/bin/s3cret"
    assert cfg.args == ["--key=s3cret"]
    assert cfg.env == {"K": "v-s3cret"}


def test_config_env_expansion_leaves_undefined_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    from yaya.plugins.mcp_bridge.config import parse_mcp_config

    monkeypatch.delenv("YAYA_NOT_A_REAL_VAR_xyzzy", raising=False)
    good, errors = parse_mcp_config({"servers": {"x": {"command": "bin", "args": ["$YAYA_NOT_A_REAL_VAR_xyzzy"]}}})
    assert errors == []
    # expandvars leaves unresolved references verbatim.
    assert good[0].args == ["$YAYA_NOT_A_REAL_VAR_xyzzy"]


# ---------------------------------------------------------------------------
# tool_factory.py schema translation + error-path coverage.
# ---------------------------------------------------------------------------


async def test_tool_factory_falls_back_when_schema_not_object(tmp_path: Path) -> None:
    """Non-object input schemas collapse to a single ``args: dict`` passthrough."""
    descriptor = MCPToolDescriptor(name="x", description="", input_schema={"type": "string"})
    client = _FakeClient(descriptors=[descriptor])
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    # Pydantic should accept an arbitrary args dict.
    instance = tool_cls.model_validate({"args": {"anything": "goes"}})
    assert instance is not None


async def test_tool_factory_falls_back_when_properties_missing(tmp_path: Path) -> None:
    """Object schema with no ``properties`` also falls back to passthrough."""
    descriptor = MCPToolDescriptor(name="x", description="", input_schema={"type": "object"})
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        _FakeClient(),  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    instance = tool_cls.model_validate({})
    assert instance is not None


async def test_tool_factory_honours_optional_with_description(tmp_path: Path) -> None:
    """Optional properties carry description and accept absence."""
    descriptor = MCPToolDescriptor(
        name="x",
        description="",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "an int"},
                "b": {"type": "number"},  # no description branch
                "c": {"type": "totally-unknown"},  # falls back to Any
                "d": "not-a-dict",  # non-dict prop schema branch
            },
            "required": ["a", 99],  # non-string required entries are ignored
        },
    )
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        _FakeClient(),  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    # Only "a" is required.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        tool_cls.model_validate({})
    instance = tool_cls.model_validate({"a": 1})
    assert instance is not None


async def test_tool_run_translates_crashed_and_unexpected_errors(tmp_path: Path) -> None:
    """Crashed / unexpected exceptions surface as the right ``ToolError`` kind."""
    from yaya.kernel.tool import ToolError
    from yaya.plugins.mcp_bridge.client import MCPServerCrashedError

    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )

    async def _crash(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise MCPServerCrashedError(137)

    client = _FakeClient(descriptors=[descriptor], call_handler=_crash)
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    instance = tool_cls.model_validate({"msg": "hi"})
    bus = EventBus()
    ctx = _make_ctx(tmp_path, bus)
    try:
        result = await instance.run(ctx)
        assert isinstance(result, ToolError)
        assert result.kind == "crashed"

        async def _boom(name: str, args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("unexpected")

        client2 = _FakeClient(descriptors=[descriptor], call_handler=_boom)
        tool_cls2 = build_mcp_tool_class(
            "local",
            descriptor,
            client2,  # type: ignore[arg-type]
            requires_approval=False,
            call_timeout_s=1.0,
        )
        instance2 = tool_cls2.model_validate({"msg": "hi"})
        result2 = await instance2.run(ctx)
        assert isinstance(result2, ToolError)
        assert result2.kind == "crashed"
    finally:
        await bus.close()


async def test_tool_run_translates_mcp_error_response(tmp_path: Path) -> None:
    """``isError: true`` responses surface as ``ToolError(kind="internal")``."""
    from yaya.kernel.tool import ToolError

    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )

    async def _error_reply(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "nope"}], "isError": True}

    client = _FakeClient(descriptors=[descriptor], call_handler=_error_reply)
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    instance = tool_cls.model_validate({"msg": "hi"})
    bus = EventBus()
    ctx = _make_ctx(tmp_path, bus)
    try:
        result = await instance.run(ctx)
        assert isinstance(result, ToolError)
        assert result.kind == "internal"
    finally:
        await bus.close()


async def test_tool_run_translates_protocol_error(tmp_path: Path) -> None:
    """MCPProtocolError → ``ToolError(kind="internal")`` with protocol-error brief."""
    from yaya.kernel.tool import ToolError
    from yaya.plugins.mcp_bridge.client import MCPProtocolError

    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )

    async def _raise_protocol(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise MCPProtocolError("malformed")

    client = _FakeClient(descriptors=[descriptor], call_handler=_raise_protocol)
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    instance = tool_cls.model_validate({"msg": "hi"})
    bus = EventBus()
    ctx = _make_ctx(tmp_path, bus)
    try:
        result = await instance.run(ctx)
        assert isinstance(result, ToolError)
        assert result.kind == "internal"
        assert "protocol" in result.brief
    finally:
        await bus.close()


async def test_tool_run_without_text_block_uses_tool_name_as_brief(tmp_path: Path) -> None:
    """When the content has no text block, brief falls back to the tool name."""
    from yaya.kernel.tool import ToolOk

    descriptor = MCPToolDescriptor(
        name="echo",
        description="",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    )

    async def _no_text(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "image", "data": "..."}], "isError": False}

    client = _FakeClient(descriptors=[descriptor], call_handler=_no_text)
    tool_cls = build_mcp_tool_class(
        "local",
        descriptor,
        client,  # type: ignore[arg-type]
        requires_approval=False,
        call_timeout_s=1.0,
    )
    instance = tool_cls.model_validate({"msg": "hi"})
    bus = EventBus()
    ctx = _make_ctx(tmp_path, bus)
    try:
        result = await instance.run(ctx)
        assert isinstance(result, ToolOk)
        assert result.brief == "echo"
    finally:
        await bus.close()


def test_tool_factory_sanitize_replaces_each_punctuation_char() -> None:
    """Each non-alnum char becomes ``_`` (no collapsing — stays one-for-one)."""
    assert mcp_tool_qualified_name("!!!", "???") == "mcp________"
    from yaya.plugins.mcp_bridge.tool_factory import _sanitize

    # Empty string hits the final fallback to "_".
    assert _sanitize("") == "_"
