"""Minimal MCP stdio client — vendored per AGENT.md §4.

The Model Context Protocol over stdio is newline-delimited
JSON-RPC 2.0: the client writes ``{"jsonrpc":"2.0",...}\\n`` on the
child's stdin and reads responses line-by-line from its stdout. The
server's stderr is a free-form log stream we drain and route through
the plugin logger.

Pulling in ``fastmcp`` / ``mcp`` would add a heavy dependency graph
(pydantic models of the full MCP namespace, HTTP transports, auth
stacks) for capabilities we don't need at 0.1. A 200-line stdio client
is the right trade-off (cf. ``docs/dev/no-agent-frameworks.md``:
"vendor a minimal implementation"). This module is scoped to the
*client* side of stdio MCP — server-side, SSE, HTTP, and OAuth are
out of scope.

Lessons honoured:

* #29 — every exception path surfaces as a structured error; callers
  translate to ``tool.error`` / ``plugin.error``.
* #31 — subprocess cancellation uses ``terminate()`` +
  ``wait_for(proc.wait(), BOUNDED)`` + ``kill()`` fallback so a
  non-cooperative child cannot leak into the next test.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPProtocolError",
    "MCPServerCrashedError",
    "MCPTimeoutError",
    "MCPToolDescriptor",
]

_logger = logging.getLogger(__name__)

_DEFAULT_TERM_GRACE_S: float = 5.0
"""Seconds we wait between ``terminate()`` and ``kill()`` (lesson #31)."""

_INIT_TIMEOUT_S: float = 15.0
"""Boot timeout for the ``initialize`` handshake + first ``tools/list``."""

# MCP protocol version we advertise on ``initialize``. Servers that only
# speak newer revisions are expected to downgrade gracefully; if they
# refuse, the handshake surfaces as :class:`MCPProtocolError`.
_MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPClientError(RuntimeError):
    """Base class for every MCP client failure surfaced by this module."""


class MCPProtocolError(MCPClientError):
    """The child process spoke MCP but the exchange violated the protocol.

    Examples: malformed JSON, missing ``jsonrpc`` key, response to an
    unknown request id, ``error`` payload on ``initialize``.
    """


class MCPServerCrashedError(MCPClientError):
    """The child process exited unexpectedly while a call was in flight.

    Carries the final return code so the caller can distinguish a clean
    exit (rare but possible on shutdown races) from a crash.
    """

    def __init__(self, returncode: int | None) -> None:
        super().__init__(f"MCP server exited (returncode={returncode})")
        self.returncode = returncode


class MCPTimeoutError(MCPClientError):
    """A request exceeded its wall-clock budget.

    Translated by the :class:`~yaya.plugins.mcp_bridge.tool_factory.MCPTool`
    wrapper to ``ToolError(kind="timeout")``.
    """


@dataclass(slots=True, frozen=True)
class MCPToolDescriptor:
    """One tool declaration as returned by an MCP ``tools/list`` call.

    Attributes:
        name: Server-side tool name. The bridge namespaces this into
            ``mcp_<server>_<name>`` before registering with yaya.
        description: Server-supplied free text; surfaced to the LLM.
        input_schema: JSON Schema dict for the tool's input. Used both
            to build a pydantic model and to forward to the LLM
            function-calling surface.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(slots=True)
class _PendingRequest:
    """One in-flight JSON-RPC request awaiting a matching response."""

    future: asyncio.Future[dict[str, Any]]
    method: str


class MCPClient:
    """Minimal MCP stdio client — one instance per configured server.

    Lifecycle::

        client = MCPClient(command, args, env=..., logger=...)
        tools = await client.start()  # spawn + initialize + tools/list
        result = await client.call_tool("echo", {"msg": "hi"})
        await client.close()

    Thread model: single asyncio loop. Not safe to share across loops.
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        *,
        env: Mapping[str, str] | None = None,
        logger: Any = None,
        term_grace_s: float = _DEFAULT_TERM_GRACE_S,
    ) -> None:
        """Record wiring; no I/O until :meth:`start`.

        Args:
            command: Executable to spawn.
            args: Additional argv entries.
            env: Extra env vars merged over :data:`os.environ`. None
                means "inherit the process env unchanged".
            logger: Optional plugin-scoped logger; falls back to the
                module logger when omitted.
            term_grace_s: Seconds between ``terminate()`` and
                ``kill()`` during :meth:`close`.
        """
        self._command = command
        self._args = args
        self._env_overrides: dict[str, str] = dict(env) if env else {}
        self._logger = logger or _logger
        self._term_grace_s = term_grace_s

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._id_counter = itertools.count(1)
        self._pending: dict[int, _PendingRequest] = {}
        self._closed = False
        # Guards writes to the child's stdin so concurrent tool calls
        # don't interleave bytes mid-line.
        self._write_lock = asyncio.Lock()

    async def start(self) -> list[MCPToolDescriptor]:
        """Spawn the child, run the ``initialize`` handshake, list tools.

        Returns:
            Every tool descriptor the server advertises. Empty list is
            valid (the server just has no tools).

        Raises:
            MCPServerCrashedError: Child exited before initialize.
            MCPTimeoutError: Handshake or tool-list timed out.
            MCPProtocolError: Malformed handshake response.
            OSError: Exec failed (binary not on PATH, permissions).
        """
        env = {**os.environ, **self._env_overrides}
        self._proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(),
            name=f"mcp-reader:{self._command}",
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(),
            name=f"mcp-stderr:{self._command}",
        )

        try:
            await asyncio.wait_for(self._initialize(), timeout=_INIT_TIMEOUT_S)
            tools = await asyncio.wait_for(self._list_tools(), timeout=_INIT_TIMEOUT_S)
        except TimeoutError as exc:
            raise MCPTimeoutError(f"MCP boot exceeded {_INIT_TIMEOUT_S}s") from exc
        return tools

    async def _initialize(self) -> None:
        """Send the MCP ``initialize`` RPC and the required ``notifications/initialized``."""
        await self._request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "yaya-mcp-bridge", "version": "0.1.0"},
            },
        )
        # Per the spec, the client MUST send this notification before
        # issuing any further requests. No response is expected.
        await self._notify("notifications/initialized", {})

    async def _list_tools(self) -> list[MCPToolDescriptor]:
        """Return the server's advertised tools."""
        result = await self._request("tools/list", {})
        raw_tools: Any = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPProtocolError("tools/list result.tools must be a list")
        tools_list: list[Any] = list(raw_tools)  # pyright: ignore[reportUnknownArgumentType]
        out: list[MCPToolDescriptor] = []
        for entry in tools_list:
            if not isinstance(entry, dict):
                continue
            entry_dict = cast("dict[str, Any]", entry)
            name: Any = entry_dict.get("name")
            if not isinstance(name, str) or not name:
                continue
            description: Any = entry_dict.get("description") or ""
            schema_raw: Any = entry_dict.get("inputSchema") or {}
            schema: dict[str, Any] = cast("dict[str, Any]", schema_raw) if isinstance(schema_raw, dict) else {}
            out.append(
                MCPToolDescriptor(
                    name=name,
                    description=str(description),
                    input_schema=schema,
                )
            )
        return out

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        """Invoke ``tool_name`` with ``arguments``; return the raw result dict.

        The returned dict is the ``result`` field of the JSON-RPC
        response — typically ``{"content": [...], "isError": bool}``.

        Raises:
            MCPServerCrashedError: Child died mid-call.
            MCPTimeoutError: Call exceeded ``timeout_s``.
            MCPProtocolError: Response error payload or malformed JSON.
        """
        try:
            return await asyncio.wait_for(
                self._request("tools/call", {"name": tool_name, "arguments": arguments}),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            raise MCPTimeoutError(f"tools/call {tool_name!r} exceeded {timeout_s}s") from exc

    async def close(self) -> None:
        """Terminate the child, cancel reader tasks, fail pending futures.

        Idempotent. Safe to call from ``on_unload`` whether the boot
        succeeded or failed mid-handshake.
        """
        if self._closed:
            return
        self._closed = True

        # Fail every pending future so awaiters don't hang on teardown.
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(MCPServerCrashedError(None))
        self._pending.clear()

        proc = self._proc
        if proc is not None and proc.returncode is None:
            # Lesson #31: terminate first, bounded wait, kill fallback.
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._term_grace_s)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reader_task = None
        self._stderr_task = None

    # -- internals ---------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and await its matching response."""
        if self._closed or self._proc is None or self._proc.stdin is None:
            raise MCPServerCrashedError(self._proc.returncode if self._proc else None)
        request_id = next(self._id_counter)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = _PendingRequest(future=future, method=method)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            await self._write_line(json.dumps(payload))
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(request_id, None)
            raise MCPServerCrashedError(self._proc.returncode) from exc

        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if self._closed or self._proc is None or self._proc.stdin is None:
            raise MCPServerCrashedError(self._proc.returncode if self._proc else None)
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await self._write_line(json.dumps(payload))

    async def _write_line(self, line: str) -> None:
        """Write ``line\\n`` to the child's stdin under the writer lock."""
        assert self._proc is not None and self._proc.stdin is not None  # noqa: S101 - module invariant: caller checked.
        stdin = self._proc.stdin
        async with self._write_lock:
            stdin.write(line.encode("utf-8") + b"\n")
            await stdin.drain()

    async def _read_loop(self) -> None:
        """Demux JSON-RPC responses from the child's stdout.

        One response line → one pending future completed. On EOF we
        fail every remaining pending future so no caller hangs.
        """
        assert self._proc is not None and self._proc.stdout is not None  # noqa: S101 - module invariant.
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    # EOF — child closed stdout.
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    self._logger.warning("MCP server emitted malformed JSON line: %r", text[:200])
                    continue
                if not isinstance(message, dict):
                    continue
                self._dispatch_message(cast("dict[str, Any]", message))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("MCP stdout reader crashed")
        finally:
            # Wake anything still waiting on a reply.
            rc = self._proc.returncode
            for pending in list(self._pending.values()):
                if not pending.future.done():
                    pending.future.set_exception(MCPServerCrashedError(rc))
            self._pending.clear()

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        """Route one decoded JSON-RPC message to the right pending future."""
        msg_id = message.get("id")
        if msg_id is None:
            # Server-initiated notification — ignored at 0.1 (we don't
            # subscribe to any MCP notifications yet).
            return
        if not isinstance(msg_id, int):
            return
        pending = self._pending.get(msg_id)
        if pending is None:
            return
        if pending.future.done():
            return
        if "error" in message:
            err: Any = message["error"]
            if isinstance(err, dict):
                err_dict: dict[str, Any] = cast("dict[str, Any]", err)
                err_msg = str(err_dict.get("message") or err_dict)
            else:
                err_msg = str(err)
            pending.future.set_exception(MCPProtocolError(f"{pending.method}: {err_msg}"))
            return
        result: Any = message.get("result")
        if not isinstance(result, dict):
            pending.future.set_exception(MCPProtocolError(f"{pending.method}: result missing or non-object"))
            return
        pending.future.set_result(cast("dict[str, Any]", result))

    async def _stderr_loop(self) -> None:
        """Drain the child's stderr into the plugin logger."""
        assert self._proc is not None and self._proc.stderr is not None  # noqa: S101 - module invariant.
        stderr = self._proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._logger.debug("mcp[%s] %s", self._command, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("MCP stderr reader crashed")
