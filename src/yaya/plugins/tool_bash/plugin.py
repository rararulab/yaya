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
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, HealthReport, KernelContext
from yaya.kernel.tool import register_tool_spec, unregister_tool_spec

_NAME = "tool-bash"
_VERSION = "0.1.0"
_TOOL_NAME = "bash"
DEFAULT_TIMEOUT_S: float = 30.0

# OpenAI-compatible function spec surfaced through
# ``yaya.kernel.tool.all_tool_specs`` so strategy plugins can
# advertise the bash tool to the LLM. The dispatch path is still the
# legacy ``on_event`` handler below — this spec is schema-only.
_BASH_TOOL_SPEC: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Run an argv-list shell command in a subprocess. "
        "Use this whenever the user asks for filesystem inspection, "
        "running a CLI tool, or anything that requires a real shell. "
        "Pass the command as a list of strings (no shell metacharacters); "
        "the first element is the executable, the rest are arguments. "
        "Returns the process stdout / stderr / exit code."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cmd"],
        "properties": {
            "cmd": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    'Argv list. Example: ["ls", "-la", "/tmp"]. '
                    "Never pass a single shell string — the tool does "
                    "not expand metacharacters."
                ),
            },
        },
    },
}


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
        """No I/O; log a DEBUG so boots are traceable.

        Also publish the tool's OpenAI function spec through
        :func:`~yaya.kernel.tool.register_tool_spec` so strategy
        plugins can enumerate the bash tool via
        :func:`~yaya.kernel.tool.all_tool_specs`. Dispatch stays on
        this plugin's ``on_event`` handler.
        """
        ctx.logger.debug("tool-bash loaded (timeout=%.1fs)", self.timeout_s)
        register_tool_spec(_TOOL_NAME, _BASH_TOOL_SPEC)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Dispatch ``tool.call.request`` for ``name == "bash"``."""
        if ev.kind != "tool.call.request":
            return
        if ev.payload.get("name") != _TOOL_NAME:
            return

        call_id = str(ev.payload.get("id", ""))
        raw_args: Any = ev.payload.get("args") or {}
        args = cast("dict[str, Any]", raw_args) if isinstance(raw_args, dict) else {}
        raw_cmd: Any = args.get("cmd")

        if not isinstance(raw_cmd, list) or not all(isinstance(x, str) for x in raw_cmd):  # pyright: ignore[reportUnknownVariableType]
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

        # ``raw_cmd`` is verified list[str] at this point.
        cmd_list: list[str] = list(cast("list[str]", raw_cmd))
        await self._run(ctx, ev, call_id, cmd_list)

    async def on_unload(self, ctx: KernelContext) -> None:
        """Drop the spec registration so hot-reload doesn't leak it."""
        unregister_tool_spec(_TOOL_NAME)

    async def health_check(self, ctx: KernelContext) -> HealthReport:
        """Verify ``bash`` is on ``$PATH``.

        Fast, local, no spawn: :func:`shutil.which` only walks
        ``PATH`` entries. ``failed`` when missing because without
        bash the tool cannot dispatch at all.
        """
        del ctx  # unused — stateless check.
        resolved = shutil.which("bash")
        if resolved is None:
            return HealthReport(
                status="failed",
                summary="bash not found on PATH",
            )
        return HealthReport(status="ok", summary=f"bash at {resolved}")

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
