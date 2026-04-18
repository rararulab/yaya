"""Tool contract v1: pydantic-validated params + ToolOk/ToolError envelope.

This module is the **authoritative** Python surface of yaya's tool contract,
a mirror of the "Tools (v1 contract)" section of
``docs/dev/plugin-protocol.md``. A :class:`Tool` subclass declares its
parameters as pydantic fields (so the JSON schema surfaced to the LLM for
function-calling is auto-derived) and implements
:meth:`Tool.run`. Returns flow back through a typed
:class:`ToolOk` / :class:`ToolError` envelope that carries a terse
``brief`` string for logs plus a :class:`DisplayBlock` for adapter-side
rendering.

The kernel-side :func:`dispatch` function is the single chokepoint the
bus wires to ``tool.call.request`` events tagged with
``schema_version="v1"``. It:

1. looks the tool up by name in the module-level registry;
2. validates the payload's ``args`` against the tool's pydantic schema;
3. runs the tool's :meth:`Tool.pre_approve` hook (default allow-all);
4. calls :meth:`Tool.run`;
5. serialises the :class:`ToolOk` / :class:`ToolError` result back onto
   the bus as ``tool.call.result`` with ``envelope=<dump>``.

Failures before ``run`` surface as ``tool.error`` events carrying a
machine-readable ``kind`` (``"validation"``, ``"not_found"``,
``"rejected"``) — the tool's own code never sees invalid input.

Backward compatibility: tools registered the legacy way (subscribing to
``tool.call.request`` themselves via their ``on_event`` handler) keep
working. The dispatcher ignores ``tool.call.request`` events that omit
``schema_version``, and a :func:`register_tool` call for a name already
claimed by a legacy plugin logs a WARNING instead of crashing.

Layering: this module lives in ``src/yaya/kernel/`` and must not import
from ``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Union, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yaya.kernel.events import Event

if TYPE_CHECKING:  # pragma: no cover - type-only import, breaks an import cycle.
    from yaya.kernel.bus import EventBus
    from yaya.kernel.plugin import KernelContext


_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DisplayBlock hierarchy.
# ---------------------------------------------------------------------------


class DisplayBlock(BaseModel):
    """Base class for UI-rendering hints carried in tool envelopes.

    A ``DisplayBlock`` tags a chunk of tool output with a ``kind`` so
    adapters (web UI, TUI, future Telegram/Slack) can render it
    natively. The three concrete subclasses shipped in 0.2 —
    :class:`TextBlock`, :class:`MarkdownBlock`, :class:`JsonBlock` —
    cover the common cases. A custom-block registry can land later
    without changing the envelope shape.

    The ``kind`` field is the discriminator used by pydantic to
    round-trip the union back into the right subclass. It is declared
    on each concrete subclass (``TextBlock``, ``MarkdownBlock``,
    ``JsonBlock``) as a :data:`~typing.Literal` so
    :func:`pydantic.Field` can dispatch without ambiguity.
    """

    model_config = ConfigDict(extra="forbid")


class TextBlock(DisplayBlock):
    """Plain-text display block.

    Adapters render this as monospace or plain text depending on
    surface; HTML rendering MUST escape.
    """

    kind: Literal["text"] = "text"
    text: str


class MarkdownBlock(DisplayBlock):
    """Markdown-formatted display block.

    Adapters run this through their markdown renderer. Trust level is
    the same as any other plugin output — adapters MUST sanitise.
    """

    kind: Literal["markdown"] = "markdown"
    markdown: str


class JsonBlock(DisplayBlock):
    """Arbitrary JSON payload for structured/tabular rendering.

    Used when a tool wants to hand the adapter machine-readable data
    (a file listing, an HTTP response, a parsed diff). The adapter
    decides whether to render as a table, a tree, or a code fence.
    """

    kind: Literal["json"] = "json"
    data: Any


# Tagged-union alias used by the envelope models. Pydantic picks the
# right subclass on deserialisation via the ``kind`` discriminator so
# ``ToolOk.model_validate(dumped)`` round-trips a ``TextBlock`` back to
# a ``TextBlock`` rather than the bare base class.
_DisplayBlockUnion = Annotated[
    Union[TextBlock, MarkdownBlock, JsonBlock],  # noqa: UP007
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Return envelope.
# ---------------------------------------------------------------------------


class ToolOk(BaseModel):
    """Successful tool outcome.

    Attributes:
        ok: Always ``True`` — discriminator for :data:`ToolReturnValue`.
        brief: Terse (≤80 char) one-liner for logs and status panes.
        display: Adapter-facing rendering hint; see :class:`DisplayBlock`.
    """

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    brief: str = Field(max_length=80)
    display: _DisplayBlockUnion


class ToolError(BaseModel):
    """Failed tool outcome with a machine-readable failure category.

    Attributes:
        ok: Always ``False`` — discriminator for :data:`ToolReturnValue`.
        kind: One of ``"validation"``, ``"timeout"``, ``"rejected"``,
            ``"crashed"``, ``"internal"``. Additional kinds may be
            introduced additively without breaking existing consumers.
        brief: Terse (≤80 char) one-liner for logs and status panes.
        display: Adapter-facing rendering hint; see :class:`DisplayBlock`.
    """

    model_config = ConfigDict(extra="forbid")

    ok: Literal[False] = False
    kind: str
    brief: str = Field(max_length=80)
    display: _DisplayBlockUnion


ToolReturnValue = Annotated[
    Union[ToolOk, ToolError],  # noqa: UP007
    Field(discriminator="ok"),
]
"""The value a :meth:`Tool.run` must return.

Discriminated by the ``ok`` field so ``pydantic.TypeAdapter`` can round-trip
an arbitrary ``dict`` back into the right concrete class.
"""


# ---------------------------------------------------------------------------
# Tool base class.
# ---------------------------------------------------------------------------


class Tool(BaseModel):
    """Base class for a single tool invocation.

    Each subclass declares:

    * its parameters as ordinary pydantic fields — these are
      runtime-validated by :func:`dispatch` before the subclass is
      even instantiated, and their JSON schema is surfaced to the LLM
      via :meth:`openai_function_spec`;
    * ``name`` and ``description`` :data:`typing.ClassVar` strings that
      flow into the LLM function-calling payload;
    * an ``async def run(self, ctx) -> ToolReturnValue`` body with the
      actual logic.

    Optional hooks:

    * :attr:`requires_approval` — if ``True``, the dispatcher calls
      :meth:`pre_approve` before :meth:`run`. The 0.2 contract only
      ships the hook shape; the real approval runtime lands in #28.
    """

    model_config = ConfigDict(extra="forbid")

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    requires_approval: ClassVar[bool] = False

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        """Execute the tool.

        Subclasses MUST override. The base implementation raises
        :class:`NotImplementedError` so a misconfigured subclass fails
        loudly instead of silently returning ``None``.

        Args:
            ctx: Per-plugin kernel view (bus emit, logger, state dir).

        Returns:
            Either a :class:`ToolOk` or a :class:`ToolError`. Anything
            else is coerced to a ``ToolError(kind="internal")`` by the
            dispatcher so the agent loop never sees a raw exception.
        """
        raise NotImplementedError

    async def pre_approve(self, ctx: KernelContext) -> bool:
        """Approval gate invoked by the dispatcher when :attr:`requires_approval` is ``True``.

        Default returns ``True``. Subclasses that need gating override
        this and return ``False`` (or raise) to abort the call. The
        dispatcher translates a ``False`` return into a
        ``tool.error`` with ``kind="rejected"``.

        The real approval runtime — prompting the user through an
        adapter, cancelling on interrupt, resuming on approval — is
        tracked in issue #28. This method is the hook shape that the
        runtime will plug into.
        """
        return True

    @classmethod
    def openai_function_spec(cls) -> dict[str, Any]:
        """Return the OpenAI function-calling descriptor for this tool.

        Derives the ``parameters`` JSON schema from the pydantic model.
        The output shape matches the ``{"name", "description",
        "parameters"}`` dict that the OpenAI chat-completions endpoint
        expects inside its ``tools`` array. Anthropic's Messages API
        accepts the same shape under a different key — adapters
        wrapping either vendor just repack this dict.

        Raises:
            ValueError: If ``name`` or ``description`` ClassVars are
                unset; a tool without them cannot be surfaced to the LLM.
        """
        if not cls.name:
            raise ValueError(
                f"Tool subclass {cls.__name__} must set `name` ClassVar before it can be surfaced to the LLM."
            )
        if not cls.description:
            raise ValueError(
                f"Tool subclass {cls.__name__} must set `description` ClassVar before it can be surfaced to the LLM."
            )
        schema = cls.model_json_schema()
        # Strip pydantic's auto-generated title — the LLM surface uses
        # ``name`` at the top level, and a duplicate title inside
        # ``parameters`` is noise at best and confusing at worst.
        schema.pop("title", None)
        return {
            "name": cls.name,
            "description": cls.description,
            "parameters": schema,
        }


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------


class ToolAlreadyRegisteredError(ValueError):
    """Raised when :func:`register_tool` sees a duplicate ``name``.

    Separate from :class:`ValueError` so tests and callers can
    distinguish the "already registered by the new contract" case from
    generic invalid-input errors. Backward-compat for legacy
    ``on_event`` tools is handled in :func:`register_tool` itself via a
    WARNING log — it does not raise.
    """


_tool_registry: dict[str, type[Tool]] = {}
"""Module-level registry of tool subclasses keyed by ``Tool.name``.

Process-global because tools are loaded once at kernel boot via plugin
``on_load`` and their classes live for the process lifetime. Tests that
need isolation call :func:`_clear_tool_registry` in a fixture.
"""

_legacy_tool_names: set[str] = set()
"""Names already claimed by legacy (``on_event``-subscribing) tool plugins.

Populated by :func:`mark_legacy_tool` so :func:`register_tool` can detect
the overlap and log a WARNING without crashing. Goal: backward compat,
not strict migration.
"""


def register_tool(tool_cls: type[Tool]) -> None:
    """Register ``tool_cls`` in the kernel's tool registry.

    Call this from a tool plugin's ``on_load``. The kernel's dispatcher
    (see :func:`dispatch`) will then handle any ``tool.call.request``
    event whose ``payload.name`` matches ``tool_cls.name`` and whose
    ``schema_version`` is ``"v1"``.

    Args:
        tool_cls: A concrete :class:`Tool` subclass.

    Raises:
        ToolAlreadyRegisteredError: If a different subclass is already
            registered under the same ``name``.
        ValueError: If ``tool_cls`` is missing its ``name`` ClassVar.
    """
    if not tool_cls.name:
        raise ValueError(f"Tool subclass {tool_cls.__name__} must set `name` ClassVar before registration.")
    existing = _tool_registry.get(tool_cls.name)
    if existing is not None:
        if existing is tool_cls:
            # Idempotent re-registration is fine — plugin hot-reload path.
            return
        raise ToolAlreadyRegisteredError(
            f"tool {tool_cls.name!r} already registered by {existing.__module__}.{existing.__qualname__}"
        )
    if tool_cls.name in _legacy_tool_names:
        _logger.warning(
            "tool %r is also claimed by a legacy on_event plugin; "
            "both paths will see tool.call.request events — "
            "duplicate results may be emitted",
            tool_cls.name,
        )
    _tool_registry[tool_cls.name] = tool_cls


def mark_legacy_tool(name: str) -> None:
    """Record that ``name`` is claimed by a legacy ``on_event`` tool plugin.

    Legacy plugins (e.g. bundled ``tool_bash``) subscribe to
    ``tool.call.request`` themselves. Calling this function before
    :func:`register_tool` lets the registry surface the collision as a
    WARNING so operators see the overlap without crashing the boot.
    """
    _legacy_tool_names.add(name)


def get_tool(name: str) -> type[Tool] | None:
    """Return the registered tool class for ``name`` or ``None``."""
    return _tool_registry.get(name)


def registered_tools() -> dict[str, type[Tool]]:
    """Return a shallow copy of the live registry (for diagnostics / tests)."""
    return dict(_tool_registry)


def _clear_tool_registry() -> None:
    """Reset both registries. Test-only; not part of the plugin ABI."""
    _tool_registry.clear()
    _legacy_tool_names.clear()


# ---------------------------------------------------------------------------
# Dispatcher.
# ---------------------------------------------------------------------------


async def dispatch(ev: Event, ctx: KernelContext) -> None:
    """Kernel-side dispatcher for ``tool.call.request`` events.

    Only handles events whose payload carries ``schema_version="v1"``
    — legacy payloads are left to whatever plugin subscribed via
    ``on_event``. That split is the backward-compat hinge.

    On validation failure, a ``tool.error`` event is emitted with
    ``kind="validation"`` and the tool's :meth:`Tool.run` is never
    called. On unknown name, ``tool.error`` with ``kind="not_found"``.
    On :meth:`Tool.pre_approve` returning ``False``, ``tool.error``
    with ``kind="rejected"``. On a run-time exception, a
    ``tool.call.result`` with a ``ToolError(kind="crashed")`` envelope
    — the kernel never lets a plugin exception escape onto the bus.

    Args:
        ev: The ``tool.call.request`` event.
        ctx: A :class:`KernelContext` scoped to the kernel itself
            (source ``"kernel"``); the dispatcher emits on behalf of
            the kernel, not any plugin.
    """
    if ev.payload.get("schema_version") != "v1":
        return

    call_id = str(ev.payload.get("id", ""))
    tool_name = str(ev.payload.get("name", ""))
    raw_args: Any = ev.payload.get("args") or {}
    args: dict[str, Any] = cast("dict[str, Any]", raw_args) if isinstance(raw_args, dict) else {}

    tool_cls = _tool_registry.get(tool_name)
    if tool_cls is None:
        await _emit_tool_error(
            ctx,
            ev,
            call_id=call_id,
            kind="not_found",
            brief=f"no tool registered under name {tool_name!r}",
        )
        return

    try:
        tool = tool_cls.model_validate(args)
    except ValidationError as exc:
        await _emit_tool_error(
            ctx,
            ev,
            call_id=call_id,
            kind="validation",
            brief=f"invalid params for tool {tool_name!r}",
            detail={"errors": exc.errors(include_url=False)},
        )
        return

    if tool.requires_approval:
        approved = await tool.pre_approve(ctx)
        if not approved:
            await _emit_tool_error(
                ctx,
                ev,
                call_id=call_id,
                kind="rejected",
                brief=f"tool {tool_name!r} rejected by pre_approve",
            )
            return

    result: Any
    try:
        result = await tool.run(ctx)
    except Exception as exc:  # pragma: no cover - safety net, exercised indirectly.
        result = ToolError(
            kind="crashed",
            brief=f"tool {tool_name!r} raised: {type(exc).__name__}"[:80],
            display=TextBlock(text=str(exc)),
        )

    if not isinstance(result, ToolOk | ToolError):
        result = ToolError(
            kind="internal",
            brief=f"tool {tool_name!r} returned non-ToolReturnValue",
            display=TextBlock(text=f"got {type(result).__name__!r}"),
        )

    await ctx.emit(
        "tool.call.result",
        {
            "id": call_id,
            "ok": result.ok,
            "envelope": result.model_dump(mode="json"),
            "request_id": ev.id,
        },
        session_id=ev.session_id,
    )


async def _emit_tool_error(
    ctx: KernelContext,
    ev: Event,
    *,
    call_id: str,
    kind: Literal["validation", "not_found", "rejected"],
    brief: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Single chokepoint for ``tool.error`` emits, so every path echoes ``request_id``."""
    payload: dict[str, Any] = {
        "id": call_id,
        "kind": kind,
        "brief": brief[:80],
        "request_id": ev.id,
    }
    if detail is not None:
        payload["detail"] = detail
    await ctx.emit("tool.error", payload, session_id=ev.session_id)


def install_dispatcher(bus: EventBus) -> None:
    """Subscribe the v1 dispatcher to ``tool.call.request`` on ``bus``.

    The dispatcher is registered under ``source="kernel"`` because it
    emits on behalf of the kernel, not a plugin. Bus-side: kernel-origin
    handler failures do NOT re-emit ``plugin.error`` (recursion guard);
    they log and drop. This matches how the bus treats its own synthetic
    events.

    This is a thin helper; callers that want to wire the dispatcher at
    a different layer (e.g. a test harness) can subscribe manually.

    Args:
        bus: A live :class:`yaya.kernel.bus.EventBus`.
    """
    # Lazy import: KernelContext lives in plugin.py, which imports from
    # events.py — no cycle with tool.py itself.
    from yaya.kernel.plugin import KernelContext

    kernel_ctx = KernelContext(
        bus=bus,
        logger=_logger,
        config={},
        state_dir=_KERNEL_STATE_DIR_SENTINEL,
        plugin_name="kernel",
    )

    async def _handle(ev: Event) -> None:
        await dispatch(ev, kernel_ctx)

    bus.subscribe("tool.call.request", _handle, source="kernel")


# The dispatcher never writes to disk; the state dir is a placeholder
# satisfying the :class:`KernelContext` constructor. Kept at module level
# so the :func:`install_dispatcher` helper does not allocate a fresh
# ``Path`` per install.
from pathlib import Path as _Path  # noqa: E402

_KERNEL_STATE_DIR_SENTINEL: _Path = _Path("/nonexistent/yaya-kernel-dispatcher")


__all__ = [
    "DisplayBlock",
    "JsonBlock",
    "MarkdownBlock",
    "TextBlock",
    "Tool",
    "ToolAlreadyRegisteredError",
    "ToolError",
    "ToolOk",
    "ToolReturnValue",
    "dispatch",
    "get_tool",
    "install_dispatcher",
    "mark_legacy_tool",
    "register_tool",
    "registered_tools",
]
