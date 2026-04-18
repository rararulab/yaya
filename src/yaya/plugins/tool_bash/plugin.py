# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Bash tool plugin implementation.

Argv-only by construction — ``shell=True`` is never used because it
would expand metacharacters in the user's string and turn the
``bash`` tool into a shell-injection hazard. Callers pass a
``list[str]`` under ``payload.args.cmd``; anything else surfaces
``tool.call.result`` with ``ok=False`` before any subprocess spawn.

Timeout defaults to 30 s; on overrun the child is killed and the
result payload reports ``error="timeout"``. ``request_id`` is echoed
on every emit so the agent loop can correlate concurrent tool calls
(lesson #15).

The file-level ``pyright: reportUnknown*=false`` pragmas silence
``Unknown`` propagation through ``Event.payload: dict[str, Any]``;
every outbound payload is a plain ``dict[str, Any]`` by construction
so the bus contract stays typed.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

_NAME = "tool-bash"
_VERSION = "0.1.0"
_TOOL_NAME = "bash"
DEFAULT_TIMEOUT_S: float = 30.0


class BashTool:
    """Bundled bash tool plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.TOOL`.
        timeout_s: Per-invocation wall-clock limit (overridable in tests).
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s

    def subscriptions(self) -> list[str]:
        """Only ``tool.call.request`` — the single request kind for this category."""
        return ["tool.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        """No I/O; log a DEBUG so boots are traceable."""
        ctx.logger.debug("tool-bash loaded (timeout=%.1fs)", self.timeout_s)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Dispatch ``tool.call.request`` for ``name == "bash"``."""
        if ev.kind != "tool.call.request":
            return
        if ev.payload.get("name") != _TOOL_NAME:
            return

        call_id = str(ev.payload.get("id", ""))
        raw_args: Any = ev.payload.get("args") or {}
        args = cast("dict[str, Any]", raw_args) if isinstance(raw_args, dict) else {}
        cmd: Any = args.get("cmd")

        cmd_list: list[Any] = list(cmd) if isinstance(cmd, list) else []
        if not (isinstance(cmd, list) and all(isinstance(x, str) for x in cmd_list)):
            await self._emit_result(
                ctx,
                ev,
                {
                    "id": call_id,
                    "ok": False,
                    "error": "cmd must be argv list (list of strings)",
                    "request_id": ev.id,
                },
            )
            return

        await self._run(ctx, ev, call_id, cast("list[str]", cmd))

    async def on_unload(self, ctx: KernelContext) -> None:
        """No resources; no-op."""

    # -- internals ------------------------------------------------------------

    async def _run(
        self,
        ctx: KernelContext,
        ev: Event,
        call_id: str,
        cmd: list[str],
    ) -> None:
        """Spawn, collect, time-limit, and emit ``tool.call.result``."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            await self._emit_result(
                ctx,
                ev,
                {
                    "id": call_id,
                    "ok": False,
                    "error": "timeout",
                    "request_id": ev.id,
                },
            )
            return

        await self._emit_result(
            ctx,
            ev,
            {
                "id": call_id,
                "ok": True,
                "value": {
                    "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                    "stderr": stderr_bytes.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode,
                },
                "request_id": ev.id,
            },
        )

    @staticmethod
    async def _emit_result(ctx: KernelContext, ev: Event, payload: dict[str, Any]) -> None:
        """Single chokepoint for ``tool.call.result`` so every path echoes ``request_id``."""
        await ctx.emit("tool.call.result", payload, session_id=ev.session_id)


__all__ = ["DEFAULT_TIMEOUT_S", "BashTool"]
