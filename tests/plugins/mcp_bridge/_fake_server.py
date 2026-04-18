"""Tiny pure-Python stdio MCP server used by the bridge integration tests.

Speaks just enough of the ``2024-11-05`` MCP wire format for the
bridge to complete an ``initialize`` + ``tools/list`` handshake and
service a single ``tools/call`` against an ``echo`` tool. No network,
no third-party dependencies — invoked by spawning ``python
_fake_server.py`` from the test process.

A second tool ``slow`` sleeps for the requested duration so the
timeout integration test has something to time out against.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "Echo back the supplied msg field.",
        "inputSchema": {
            "type": "object",
            "properties": {"msg": {"type": "string", "description": "Text to echo."}},
            "required": ["msg"],
        },
    },
    {
        "name": "slow",
        "description": "Sleep for `seconds` then return.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
]


def _send(message: dict[str, Any]) -> None:
    """Write ``message`` as one newline-delimited JSON-RPC line on stdout."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _reply(request_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error_reply(request_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    params = message.get("params") or {}
    msg_id = message.get("id")

    if method == "initialize":
        _reply(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
            },
        )
        return
    if method == "notifications/initialized":
        # Notification — no response.
        return
    if method == "tools/list":
        _reply(msg_id, {"tools": _TOOLS})
        return
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            msg = str(arguments.get("msg", ""))
            _reply(
                msg_id,
                {
                    "content": [{"type": "text", "text": msg}],
                    "isError": False,
                },
            )
            return
        if name == "slow":
            time.sleep(float(arguments.get("seconds", 0)))
            _reply(msg_id, {"content": [{"type": "text", "text": "done"}], "isError": False})
            return
        _error_reply(msg_id, -32601, f"unknown tool: {name!r}")
        return

    if msg_id is not None:
        _error_reply(msg_id, -32601, f"unknown method: {method!r}")


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        _handle(message)


if __name__ == "__main__":
    main()
