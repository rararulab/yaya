"""Agent tool plugin implementation.

The plugin registers one v1 tool, ``agent``, that forks the caller's
:class:`~yaya.kernel.session.Session` and drives a child turn on the
same event bus. Depth is encoded in the child session id
(``<parent>::agent::<hex>``); depth-limit and allowlist checks happen
synchronously inside :meth:`AgentTool.run` before anything is spawned
so a recursion runaway or capability-escalation attempt fails fast with
a :class:`~yaya.kernel.tool.ToolError`.

The happy path:

1. :meth:`AgentTool.run` resolves the parent :class:`Session` held by
   :class:`_Runtime` (populated from the plugin's own ``on_load``
   context, see :meth:`AgentPlugin.on_load`).
2. It calls ``parent.fork(child_id)``; the overlay tape isolates child
   writes from the parent.
3. It subscribes to the bus on two kinds (``assistant.message.done``
   and ``tool.call.request``), filtered by
   ``ev.session_id == child_id``.
4. It publishes ``user.message.received`` on the child session; the
   kernel's running :class:`~yaya.kernel.loop.AgentLoop` drives the
   turn through the strategy of its choice.
5. An ``assistant.message.done`` on the child session resolves the
   completion future and the tool returns the ``content`` as a
   :class:`~yaya.kernel.tool.ToolOk`.

Extension events under the ``x.agent.*`` namespace surface progress to
adapters without polluting the closed public event catalog
(GOAL.md principle #3).
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, cast, override
from uuid import uuid4

from pydantic import ConfigDict, Field

from yaya.kernel.bus import EventBus, Subscription
from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext
from yaya.kernel.session import Session
from yaya.kernel.tool import TextBlock, Tool, ToolError, ToolOk, ToolReturnValue, register_tool, unregister_tool

_NAME = "agent-tool"
_VERSION = "0.1.0"
_TOOL_NAME = "agent"

DEFAULT_MAX_STEPS: int = 20
"""Default cap on strategy steps per sub-agent run (see issue #34 spec)."""

DEFAULT_MAX_WALL_SECONDS: float = 300.0
"""Default wall-clock deadline per sub-agent run."""

DEFAULT_MAX_DEPTH: int = 5
"""Default max chain of nested sub-agents (parent -> child -> ...)."""

_CHILD_SEP = "::agent::"
"""Separator baked into child session ids; also the depth counter."""

# Plugin-private extension event kinds. Routed through the bus per
# GOAL.md principle #3 ``x.<plugin>.<kind>`` namespace.
EVENT_SUBAGENT_STARTED = "x.agent.subagent.started"
EVENT_SUBAGENT_COMPLETED = "x.agent.subagent.completed"
EVENT_SUBAGENT_FAILED = "x.agent.subagent.failed"
EVENT_ALLOWLIST_NARROWED = "x.agent.allowlist.narrowed"

_AGENT_SESSION = "_bridge:agent-tool"
"""Stable session id for plugin-private extension events."""


class _Runtime:
    """Module-level binding of kernel objects the tool needs at run time.

    Populated from :meth:`AgentPlugin.on_load`; read by
    :meth:`AgentTool.run`. Plugin-global by necessity — pydantic Tool
    instances are stateless envelopes constructed per call, and the v1
    dispatcher hands them a bare :class:`KernelContext` without a
    session attached. The alternative (threading the session through
    every tool.call.request payload) would ripple into the closed
    public catalog; keeping the coupling inside one plugin is cheaper.
    """

    session: Session | None = None
    bus: EventBus | None = None
    # Plugin-owned KernelContext cached during on_load so AgentTool.run
    # can emit x.agent.* events with source="agent-tool". The ctx the v1
    # dispatcher hands to run() carries plugin_name="kernel" (see
    # kernel/tool.py::install_dispatcher), which would mis-attribute
    # plugin-private extension events to the kernel itself.
    plugin_ctx: KernelContext | None = None
    max_depth: int = DEFAULT_MAX_DEPTH


def _depth_of(session_id: str) -> int:
    """Return how many ``::agent::`` hops are baked into ``session_id``.

    A root session returns 0; a first-generation child returns 1.
    """
    return session_id.count(_CHILD_SEP)


def _resolve_runtime() -> tuple[EventBus, Session] | ToolError:
    """Fetch the cached (bus, session) pair or surface a ``ToolError``.

    Returned directly to the caller instead of raising so the tool's
    ``run`` body stays a single try/finally around the fork lifecycle.
    """
    bus = _Runtime.bus
    session = _Runtime.session
    if bus is None or session is None:
        brief = "agent-tool runtime not bound"
        return ToolError(
            kind="internal",
            brief=brief,
            display=TextBlock(
                text=(
                    f"{brief}; the plugin's on_load must run before the "
                    "tool is invoked (bus and parent session are cached there)."
                ),
            ),
        )
    return bus, session


def _subscribe_child(
    bus: EventBus,
    child_id: str,
    allowlist: list[str] | None,
) -> tuple[asyncio.Future[str], list[str], list[Subscription]]:
    """Wire the child-session listeners and return the coordination handles.

    Installs two subscriptions, both filtered to ``ev.session_id ==
    child_id`` so the bridge cannot accidentally consume another
    session's traffic:

    * ``assistant.message.done`` — resolves ``completion`` with
      ``payload["content"]`` the first time the child publishes one.
    * ``tool.call.request`` — observational filter; when ``allowlist``
      is not None and the requested ``name`` is outside it, append to
      ``forbidden_hits`` so the caller can surface
      ``x.agent.allowlist.narrowed`` once the run finishes.

    Tool failures inside the sub-agent (``validation``, ``not_found``,
    ``rejected``) round-trip through the v1 dispatcher as
    ``tool.call.result`` envelopes with ``ok=False`` — the child's
    strategy loop handles them like any tool failure. Fatal dispatcher
    crashes (``internal`` / ``crashed``) also land inside the result
    envelope (see :func:`yaya.kernel.tool.dispatch`); the sub-agent
    runs until its own guard (timeout, max steps, assistant.done)
    rather than short-circuiting on a single tool error.

    Returns:
        ``(completion, forbidden_hits, subscriptions)``. The caller is
        responsible for tearing down each :class:`Subscription` in a
        ``finally`` block so a cancelled parent turn does not leak
        handlers onto the bus.
    """
    loop = asyncio.get_running_loop()
    completion: asyncio.Future[str] = loop.create_future()
    forbidden_hits: list[str] = []
    allowed_set: set[str] | None = set(allowlist) if allowlist is not None else None

    async def _on_done(ev: Event) -> None:
        if ev.session_id != child_id or completion.done():
            return
        raw = ev.payload.get("content")
        content = raw if isinstance(raw, str) else ""
        completion.set_result(content)

    async def _on_request(ev: Event) -> None:
        if ev.session_id != child_id or allowed_set is None:
            return
        raw_name = ev.payload.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        if name and name not in allowed_set:
            forbidden_hits.append(name)

    subs: list[Subscription] = [
        bus.subscribe("assistant.message.done", _on_done, source="agent-tool"),
        bus.subscribe("tool.call.request", _on_request, source="agent-tool"),
    ]
    return completion, forbidden_hits, subs


class AgentTool(Tool):
    """Spawn a sub-agent to handle a focused sub-task.

    The tool forks the caller's :class:`Session` via
    :meth:`Session.fork`, pumps the resulting child session through the
    same kernel :class:`~yaya.kernel.loop.AgentLoop` the parent uses,
    and returns the sub-agent's final
    ``assistant.message.done.content`` as a
    :class:`~yaya.kernel.tool.ToolOk` envelope.

    Approval is mandatory (``requires_approval = True``): spawning a
    sub-agent can amplify capability and burn arbitrary LLM tokens, so
    the user sees one prompt per spawn. Tool calls *inside* the
    sub-agent go through the same approval runtime as the parent's.
    """

    model_config = ConfigDict(extra="forbid")

    name: ClassVar[str] = _TOOL_NAME
    description: ClassVar[str] = "Spawn a sub-agent to handle a focused sub-task."
    requires_approval: ClassVar[bool] = True

    goal: str = Field(min_length=1, description="Task description handed to the sub-agent.")
    strategy: str = Field(
        default="react",
        description="Strategy plugin id (informational; active strategy is chosen by the kernel).",
    )
    tools: list[str] | None = Field(
        default=None,
        description=(
            "Observational allowlist at 0.2: tool names outside this list "
            "are recorded via x.agent.allowlist.narrowed but not blocked. "
            "Hard enforcement lands in a later release. None = inherit parent."
        ),
    )
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=200)
    max_wall_seconds: float = Field(default=DEFAULT_MAX_WALL_SECONDS, gt=0.0, le=3600.0)

    @override
    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        """Fork the parent session, drive the child turn, return final text.

        Thin top-level orchestrator; per-phase work lives in the helpers
        (``_resolve_runtime``, ``_subscribe_child``, ``_drive_child``)
        so each stays under the cyclomatic-complexity bar.
        """
        binding = _resolve_runtime()
        if isinstance(binding, ToolError):
            return binding
        bus, parent = binding

        parent_id = parent.session_id
        if _depth_of(parent_id) >= _Runtime.max_depth:
            brief = f"max depth {_Runtime.max_depth} exceeded"
            return ToolError(
                kind="rejected",
                brief=brief,
                display=TextBlock(text=f"{brief}; refusing to spawn another sub-agent."),
            )

        child_id = f"{parent_id}{_CHILD_SEP}{uuid4().hex[:8]}"
        child = parent.fork(child_id)
        completion, forbidden_hits, subs = _subscribe_child(bus, child_id, self.tools)

        await _emit(
            ctx,
            EVENT_SUBAGENT_STARTED,
            {
                "parent_id": parent_id,
                "child_id": child_id,
                "goal": self.goal,
                "strategy": self.strategy,
                "tools": list(self.tools) if self.tools is not None else None,
            },
        )

        try:
            return await self._drive_child(
                ctx=ctx,
                child=child,
                child_id=child_id,
                completion=completion,
                forbidden_hits=forbidden_hits,
            )
        except asyncio.CancelledError:
            await _emit(
                ctx,
                EVENT_SUBAGENT_FAILED,
                {"child_id": child_id, "reason": "cancelled"},
            )
            raise
        finally:
            for sub in subs:
                sub.unsubscribe()

    async def _drive_child(
        self,
        *,
        ctx: KernelContext,
        child: Session,
        child_id: str,
        completion: asyncio.Future[str],
        forbidden_hits: list[str],
    ) -> ToolReturnValue:
        """Kick the child turn, wait for ``assistant.message.done``, envelope it."""
        # The kernel's running AgentLoop (subscribed to this kind on any
        # session) picks it up and drives the strategy/LLM/tool dance.
        await ctx.emit(
            "user.message.received",
            {"text": self.goal},
            session_id=child_id,
        )

        try:
            final_text = await asyncio.wait_for(completion, timeout=self.max_wall_seconds)
        except TimeoutError:
            await _emit(
                ctx,
                EVENT_SUBAGENT_FAILED,
                {"child_id": child_id, "reason": "timeout"},
            )
            return ToolError(
                kind="timeout",
                brief=f"subagent exhausted {self.max_wall_seconds:.0f}s budget",
                display=TextBlock(
                    text=f"sub-agent on session {child_id!r} did not finish within "
                    f"{self.max_wall_seconds:.0f}s; child tape is preserved for inspection.",
                ),
            )

        # Child completed. Record how many entries it produced so callers
        # can correlate step count (we don't fish through the tape for
        # real step counts — the loop's own ``max_iterations`` is the
        # authoritative cap).
        child_entries = await child.entries()
        steps_used = len(child_entries)

        await _emit(
            ctx,
            EVENT_SUBAGENT_COMPLETED,
            {
                "child_id": child_id,
                "final_text": final_text,
                "steps_used": steps_used,
                "forbidden_tool_hits": list(forbidden_hits),
            },
        )

        if forbidden_hits:
            # Surface a narrowed-allowlist breadcrumb so operators see the
            # refused tool names without the parent turn failing.
            await _emit(
                ctx,
                EVENT_ALLOWLIST_NARROWED,
                {
                    "child_id": child_id,
                    "attempted": list(forbidden_hits),
                    "allowed": list(self.tools or []),
                },
            )

        return ToolOk(
            brief=_truncate(f"subagent done ({steps_used} entries)", 80),
            display=TextBlock(text=final_text),
        )


def _truncate(text: str, n: int) -> str:
    """Pydantic's ``brief`` is ≤80 chars — clamp defensively."""
    return text if len(text) <= n else text[: n - 1] + "…"


async def _emit(ctx: KernelContext, kind: str, payload: dict[str, Any]) -> None:
    """Publish ``kind`` on the agent-bridge session, swallowing emit errors.

    Lesson #2: plugin-private extension events use a stable bridge
    session so they never interleave with conversation FIFOs.

    Routes through the plugin's own cached :class:`KernelContext` when
    available so ``x.agent.*`` events stamp ``source="agent-tool"``.
    The ``ctx`` the v1 dispatcher hands to :meth:`AgentTool.run` carries
    ``plugin_name="kernel"`` (see ``kernel/tool.py::install_dispatcher``);
    using it directly would mis-attribute plugin-private extension
    events to the kernel itself. Falling back to ``ctx`` keeps the
    helper usable from tests or lifecycle paths that construct their
    own context before ``on_load`` has bound the plugin ctx.
    """
    emit_ctx = _Runtime.plugin_ctx or ctx
    try:
        await emit_ctx.emit(kind, payload, session_id=_AGENT_SESSION)
    except Exception:  # pragma: no cover - defensive, emit errors are logged.
        emit_ctx.logger.exception("agent-tool: failed to emit %s", kind)


class AgentPlugin:
    """Bundled agent-tool plugin.

    Category :class:`Category.TOOL`. Registers :class:`AgentTool` on
    ``on_load`` and caches the kernel-side session + bus pair on
    :class:`_Runtime` so :meth:`AgentTool.run` can reach them without
    the v1 dispatcher threading a session into its KernelContext.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self, *, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        """Record the depth knob; no I/O until :meth:`on_load`."""
        self._max_depth = max_depth

    def subscriptions(self) -> list[str]:
        """Bridge is a pure tool provider — no bus subscriptions.

        Inbound ``tool.call.request`` events for ``name="agent"`` flow
        through the kernel's v1 dispatcher (see
        :func:`yaya.kernel.tool.dispatch`), which looks up
        :class:`AgentTool` in the registry populated during
        :meth:`on_load`.
        """
        return []

    async def on_load(self, ctx: KernelContext) -> None:
        """Register :class:`AgentTool` and bind the runtime session + bus.

        Caches ``ctx`` on :class:`_Runtime` so :func:`_emit` can emit
        ``x.agent.*`` events with ``source="agent-tool"`` — the ctx the
        v1 dispatcher hands to :meth:`AgentTool.run` stamps
        ``source="kernel"`` (see
        ``kernel/tool.py::install_dispatcher``), which would
        mis-attribute plugin-private extension events.
        """
        register_tool(AgentTool)
        _Runtime.session = ctx.session
        _Runtime.bus = ctx.bus
        _Runtime.plugin_ctx = ctx
        configured = cast("int", ctx.config.get("max_depth", self._max_depth)) if ctx.config else self._max_depth
        _Runtime.max_depth = int(configured)
        ctx.logger.debug(
            "agent-tool loaded (max_depth=%d, session=%r)",
            _Runtime.max_depth,
            ctx.session.session_id if ctx.session is not None else None,
        )

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """No-op — see :meth:`subscriptions`."""

    async def on_unload(self, ctx: KernelContext) -> None:
        """Unregister :class:`AgentTool` and drop cached bindings. Idempotent.

        Tool unregistration happens before the runtime pointers are
        cleared so a dispatch racing the unload finds either the tool
        (and a still-bound runtime) or no tool at all — never a tool
        pointing at a ``None`` bus/session (#90).
        """
        unregister_tool(_TOOL_NAME)
        _Runtime.session = None
        _Runtime.bus = None
        _Runtime.plugin_ctx = None


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_MAX_WALL_SECONDS",
    "EVENT_ALLOWLIST_NARROWED",
    "EVENT_SUBAGENT_COMPLETED",
    "EVENT_SUBAGENT_FAILED",
    "EVENT_SUBAGENT_STARTED",
    "AgentPlugin",
    "AgentTool",
]
