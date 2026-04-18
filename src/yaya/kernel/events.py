"""Closed public event catalog + envelope for the yaya kernel.

This module is the **authoritative** Python-side mirror of the event taxonomy
defined in ``docs/dev/plugin-protocol.md``. The set of public event kinds is
**closed at 1.0**: introducing a new public kind is a governance change and
requires amending this module, ``docs/dev/plugin-protocol.md``, and
``GOAL.md`` in the same PR. Plugin-private events use the
``x.<plugin>.<kind>`` extension namespace and route through the bus without
type-checking.

Layering: this module lives in ``src/yaya/kernel/`` and must not import from
``cli``, ``plugins``, or ``core``.

Note:
    This module deliberately does NOT use ``from __future__ import annotations``.
    ``typing.NotRequired`` inside :class:`TypedDict` bodies must be evaluated
    at class-construction time for ``__required_keys__`` / ``__optional_keys__``
    to reflect the intended partition; PEP 563 string annotations defer that
    evaluation and collapse every field into ``__required_keys__``.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict, get_args

# ---------------------------------------------------------------------------
# Public event kinds — closed catalog (frozen at 1.0).
# Any change to this Literal is a governance amendment.
# ---------------------------------------------------------------------------

PublicEventKind = Literal[
    # User input (adapter → kernel).
    "user.message.received",
    "user.interrupt",
    # Assistant output (kernel → adapters).
    "assistant.message.delta",
    "assistant.message.done",
    # LLM invocation (kernel ↔ llm-provider).
    "llm.call.request",
    "llm.call.delta",
    "llm.call.response",
    "llm.call.error",
    # Tool execution (kernel ↔ tool).
    "tool.call.request",
    "tool.call.start",
    "tool.call.result",
    "tool.error",
    # Memory (kernel ↔ memory).
    "memory.query",
    "memory.write",
    "memory.result",
    # Strategy (kernel ↔ strategy).
    "strategy.decide.request",
    "strategy.decide.response",
    # Plugin lifecycle (kernel → all).
    "plugin.loaded",
    "plugin.reloaded",
    "plugin.removed",
    "plugin.error",
    # Kernel (kernel → all).
    "kernel.ready",
    "kernel.shutdown",
    "kernel.error",
]

PUBLIC_EVENT_KINDS: frozenset[str] = frozenset(get_args(PublicEventKind))
"""Runtime-accessible set of the closed public event kinds."""


# ---------------------------------------------------------------------------
# Per-kind payload TypedDicts.
#
# Shapes mirror the tables in ``docs/dev/plugin-protocol.md``. These are
# structural hints for type-checkers; the bus itself does not enforce them at
# runtime (payloads are plain dicts on the wire).
# ---------------------------------------------------------------------------


class Attachment(TypedDict, total=False):
    """Opaque user-message attachment; shape owned by the adapter producing it."""

    kind: str
    uri: str
    mime: str


class Message(TypedDict, total=False):
    """Single chat-style message passed to an LLM provider."""

    role: str
    content: str


class ToolCall(TypedDict, total=False):
    """A tool invocation decided by the LLM or strategy."""

    id: str
    name: str
    args: dict[str, Any]


class ToolSchema(TypedDict, total=False):
    """JSON-schema-ish description of a tool advertised to the LLM."""

    name: str
    description: str
    json_schema: dict[str, Any]


class Usage(TypedDict, total=False):
    """Token accounting returned by an LLM provider."""

    input_tokens: int
    output_tokens: int


class MemoryEntry(TypedDict, total=False):
    """A memory row that memory plugins read and write."""

    id: str
    text: str
    meta: dict[str, Any]


class AgentLoopState(TypedDict, total=False):
    """Snapshot of the agent loop handed to a strategy for its decision."""

    session_id: str
    step: int
    messages: list[Message]
    last_tool_result: dict[str, Any] | None


# --- User input ------------------------------------------------------------


class UserMessageReceivedPayload(TypedDict):
    """``user.message.received`` — text the adapter just received from the user."""

    text: str
    attachments: NotRequired[list[Attachment]]


class UserInterruptPayload(TypedDict, total=False):
    """``user.interrupt`` — user-requested end-of-turn; payload is empty by design."""


# --- Assistant output ------------------------------------------------------


class AssistantMessageDeltaPayload(TypedDict):
    """``assistant.message.delta`` — one streaming chunk of assistant content."""

    content: str


class AssistantMessageDonePayload(TypedDict):
    """``assistant.message.done`` — terminal assistant message for a turn."""

    content: str
    tool_calls: list[ToolCall]


# --- LLM invocation --------------------------------------------------------


class LlmCallRequestPayload(TypedDict):
    """``llm.call.request`` — kernel asks a provider plugin to run a completion."""

    provider: str
    model: str
    messages: list[Message]
    params: dict[str, Any]
    tools: NotRequired[list[ToolSchema]]


class LlmCallDeltaPayload(TypedDict):
    """``llm.call.delta`` — one streaming chunk from an llm-provider.

    The v1 ``llm-provider`` contract (see :mod:`yaya.kernel.llm`) yields
    deltas as it consumes the provider's async iterator; the loop
    re-emits each as an ``llm.call.delta`` on the bus so adapters can
    render progressive output and observability sinks can count
    tokens in flight.

    At most one of ``content`` / ``tool_call_partial`` is populated per
    delta. ``request_id`` mirrors the originating ``llm.call.request``
    event id for correlation — same convention as the other ``llm.*``
    events.
    """

    content: NotRequired[str]
    tool_call_partial: NotRequired[dict[str, Any]]
    request_id: NotRequired[str]


class LlmCallResponsePayload(TypedDict):
    """``llm.call.response`` — provider's completion result.

    ``request_id`` mirrors the originating ``llm.call.request`` event id so the
    kernel agent loop (see ``yaya.kernel.loop``) can correlate concurrent
    in-flight calls. Optional for backwards compatibility with hand-crafted
    fixtures but required in practice for the loop to observe the response.

    ``usage`` is the :class:`Usage` TypedDict at the bus level; the v1
    llm-provider contract populates it from
    :class:`yaya.kernel.llm.TokenUsage` via ``TokenUsage.model_dump()``
    — the additional cache-aware keys flow through as extra dict
    entries without violating the TypedDict shape.
    """

    usage: Usage
    text: NotRequired[str]
    tool_calls: NotRequired[list[ToolCall]]
    request_id: NotRequired[str]


class LlmCallErrorPayload(TypedDict):
    """``llm.call.error`` — provider failure with optional retry hint.

    ``request_id`` mirrors the originating ``llm.call.request`` event id for
    agent-loop correlation (see :class:`yaya.kernel.loop.AgentLoop`).

    ``kind`` is the v1-contract error classifier. Values mirror the
    :class:`~yaya.kernel.llm.ChatProviderError` hierarchy:

    * ``"connection"`` — :class:`~yaya.kernel.llm.APIConnectionError`
    * ``"timeout"`` — :class:`~yaya.kernel.llm.APITimeoutError`
    * ``"status"`` — :class:`~yaya.kernel.llm.APIStatusError` (pair
      with ``status_code``)
    * ``"empty"`` — :class:`~yaya.kernel.llm.APIEmptyResponseError`
    * ``"other"`` — anything else converted via
      :func:`~yaya.kernel.llm.openai_to_chat_provider_error` /
      :func:`~yaya.kernel.llm.anthropic_to_chat_provider_error` /
      :func:`~yaya.kernel.llm.convert_httpx_error`.

    Legacy providers that pre-date the v1 contract (see
    :mod:`yaya.plugins.llm_openai`, :mod:`yaya.plugins.llm_echo`) omit
    ``kind``; consumers MUST treat a missing ``kind`` as
    ``"other"``.
    """

    error: str
    retry_after_s: NotRequired[float]
    request_id: NotRequired[str]
    kind: NotRequired[Literal["connection", "timeout", "status", "empty", "other"]]
    status_code: NotRequired[int]


# --- Tool execution --------------------------------------------------------


class ToolCallRequestPayload(TypedDict):
    """``tool.call.request`` — kernel asks a tool plugin to run.

    ``schema_version`` is the v1-contract toggle (see
    :mod:`yaya.kernel.tool`). When present and equal to ``"v1"`` the
    kernel's tool dispatcher validates ``args`` against the pydantic
    schema declared by the registered :class:`~yaya.kernel.tool.Tool`
    subclass before any plugin code runs. When absent, the event falls
    through to whatever plugin subscribed via ``on_event`` — the
    pre-0.2 shape, preserved for backward compatibility.

    ``request_id`` mirrors behaviour on other request kinds: downstream
    results echo it as ``request_id`` on the corresponding result event
    so the agent loop can correlate concurrent tool calls.
    """

    id: str
    name: str
    args: dict[str, Any]
    schema_version: NotRequired[Literal["v1"]]
    request_id: NotRequired[str]


class ToolCallStartPayload(TypedDict):
    """``tool.call.start`` — broadcast to adapters so the UI can render progress."""

    id: str
    name: str
    args: dict[str, Any]


class ToolCallResultPayload(TypedDict):
    """``tool.call.result`` — tool plugin's outcome.

    ``request_id`` mirrors the originating ``tool.call.request`` event id so
    the agent loop can correlate per-call results when multiple tools run
    back-to-back. The ``id`` field remains the stable logical tool-call id
    assigned by the LLM.
    """

    id: str
    ok: bool
    value: NotRequired[Any]
    error: NotRequired[str]
    envelope: NotRequired[dict[str, Any]]
    request_id: NotRequired[str]


class ToolErrorPayload(TypedDict):
    """``tool.error`` — kernel rejects a ``tool.call.request`` before dispatch.

    Emitted by :func:`yaya.kernel.tool.dispatch` when the v1 contract
    refuses a call *before* the tool's :meth:`~yaya.kernel.tool.Tool.run`
    executes. Distinct from ``tool.call.result`` because the target tool
    never ran — adapters render these differently (usually a red banner
    at the originating turn, not a tool-pane update).

    Fields:
        id: Logical tool-call id carried by the originating request.
        kind: ``"validation"`` (params failed the pydantic schema),
            ``"not_found"`` (no tool registered under ``payload.name``),
            or ``"rejected"`` (the tool's ``pre_approve`` hook returned
            ``False``).
        brief: One-liner (≤80 char) suitable for log lines.
        detail: Optional structured context — e.g. ``pydantic``'s
            ``errors()`` list for ``kind="validation"``.
        request_id: Mirror of the originating ``tool.call.request`` id.
    """

    id: str
    kind: Literal["validation", "not_found", "rejected"]
    brief: str
    detail: NotRequired[dict[str, Any]]
    request_id: NotRequired[str]


# --- Memory ----------------------------------------------------------------


class MemoryQueryPayload(TypedDict):
    """``memory.query`` — kernel asks a memory plugin for ``k`` relevant entries."""

    query: str
    k: int


class MemoryWritePayload(TypedDict):
    """``memory.write`` — kernel asks a memory plugin to persist one entry."""

    entry: MemoryEntry


class MemoryResultPayload(TypedDict):
    """``memory.result`` — memory plugin's hits list.

    ``request_id`` mirrors the originating ``memory.query`` event id so the
    agent loop can correlate concurrent queries on the same session.
    """

    hits: list[MemoryEntry]
    request_id: NotRequired[str]


# --- Strategy --------------------------------------------------------------


class StrategyDecideRequestPayload(TypedDict):
    """``strategy.decide.request`` — kernel asks the active strategy for a next step."""

    state: AgentLoopState


class StrategyDecideResponsePayload(TypedDict, total=False):
    """``strategy.decide.response`` — strategy's chosen next step.

    ``next`` is one of ``"llm" | "tool" | "memory" | "done"``; additional keys
    describe the arguments for that step (kept as an open dict at the kernel
    level — strategy plugins own their own schema).

    ``request_id`` mirrors the originating ``strategy.decide.request`` event
    id so the agent loop can correlate the decision with the specific step
    it requested.
    """

    next: Literal["llm", "tool", "memory", "done"]
    request_id: str


# --- Plugin lifecycle ------------------------------------------------------


class PluginLoadedPayload(TypedDict):
    """``plugin.loaded`` — a plugin registered successfully."""

    name: str
    version: str
    category: str


class PluginReloadedPayload(TypedDict):
    """``plugin.reloaded`` — hot-reload completed for a plugin."""

    name: str
    version: str


class PluginRemovedPayload(TypedDict):
    """``plugin.removed`` — a plugin was unloaded (manual or failure-triggered)."""

    name: str


class PluginErrorPayload(TypedDict):
    """``plugin.error`` — a plugin's handler raised or timed out.

    The kernel synthesizes this event on behalf of the failing plugin. Plugin
    code must not emit ``plugin.error`` directly.

    Optional fields are populated when the failure surfaces through the bus
    handler-isolation path (``EventBus._report_handler_failure``):

    * ``kind`` — the exception subclass name (``"PluginError"`` or a
      plugin-defined subclass like ``"OpenAIError"``); the literal
      ``"plugin_error"`` for non-:class:`PluginError` exceptions.
    * ``error_hash`` — first 8 hex chars of a SHA-1 over the formatted
      traceback. Stable across identical failure modes so operators can
      de-dup repeats in a log scrape.
    """

    name: str
    error: str
    kind: NotRequired[str]
    error_hash: NotRequired[str]


# --- Kernel ----------------------------------------------------------------


class KernelReadyPayload(TypedDict):
    """``kernel.ready`` — kernel boot finished, plugins loaded."""

    version: str


class KernelShutdownPayload(TypedDict):
    """``kernel.shutdown`` — kernel is stopping; adapters should drain."""

    reason: str


class KernelErrorPayload(TypedDict):
    """``kernel.error`` — the kernel itself failed; ``yaya serve`` exits non-zero.

    Optional ``detail`` carries structured context (e.g. the offending
    strategy ``next`` value, raw tool args) for machine-parsing by adapters.
    """

    source: str
    message: str
    detail: NotRequired[dict[str, Any]]


# ---------------------------------------------------------------------------
# Envelope + factory.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Event:
    """The event envelope carried on the bus.

    Attributes:
        id: uuid4 hex, kernel-assigned on publish.
        kind: Dotted identifier; either a member of ``PublicEventKind`` or
            an extension name starting with ``x.``.
        session_id: Conversation scope. Plugin-private events may use any
            stable id — the bus serializes delivery per ``session_id``.
        ts: Kernel-assigned unix epoch seconds.
        source: Plugin name that emitted the event, or the literal ``"kernel"``
            for kernel-origin events (``kernel.*``, synthetic ``plugin.error``).
        payload: Kind-specific dict; shape per ``docs/dev/plugin-protocol.md``.
    """

    id: str
    kind: str
    session_id: str
    ts: float
    source: str
    payload: dict[str, Any] = field(default_factory=lambda: {})


def new_event(
    kind: str,
    payload: dict[str, Any],
    *,
    session_id: str,
    source: str,
) -> Event:
    """Create a validated :class:`Event` envelope.

    Assigns a fresh ``id`` (uuid4 hex) and a kernel-assigned timestamp. A
    ``kind`` not prefixed with ``x.`` must be a member of the closed
    ``PublicEventKind`` catalog; otherwise :class:`ValueError` is raised and
    names the catalog so the author knows where to propose the new kind.

    Args:
        kind: Public kind from :data:`PUBLIC_EVENT_KINDS` or an extension
            kind starting with ``x.``.
        payload: Kind-specific dict. Shape per ``docs/dev/plugin-protocol.md``;
            not validated here (static type-checkers enforce this via the
            per-kind :class:`TypedDict`\\ s above).
        session_id: Conversation / routing scope.
        source: Emitter — plugin name, or ``"kernel"`` for kernel-origin events.

    Returns:
        A fully populated :class:`Event`.

    Raises:
        ValueError: If ``kind`` is not in the closed public catalog and does
            not start with ``x.``.
    """
    if not kind.startswith("x.") and kind not in PUBLIC_EVENT_KINDS:
        raise ValueError(
            f"unknown public event kind {kind!r}; "
            f"the closed catalog is defined in yaya.kernel.events.PublicEventKind "
            f"(see docs/dev/plugin-protocol.md). Extension events must be "
            f"prefixed with 'x.<plugin>.'."
        )
    return Event(
        id=uuid.uuid4().hex,
        kind=kind,
        session_id=session_id,
        ts=time.time(),
        source=source,
        payload=payload,
    )


__all__ = [
    "PUBLIC_EVENT_KINDS",
    "AgentLoopState",
    "AssistantMessageDeltaPayload",
    "AssistantMessageDonePayload",
    "Attachment",
    "Event",
    "KernelErrorPayload",
    "KernelReadyPayload",
    "KernelShutdownPayload",
    "LlmCallDeltaPayload",
    "LlmCallErrorPayload",
    "LlmCallRequestPayload",
    "LlmCallResponsePayload",
    "MemoryEntry",
    "MemoryQueryPayload",
    "MemoryResultPayload",
    "MemoryWritePayload",
    "Message",
    "PluginErrorPayload",
    "PluginLoadedPayload",
    "PluginReloadedPayload",
    "PluginRemovedPayload",
    "PublicEventKind",
    "StrategyDecideRequestPayload",
    "StrategyDecideResponsePayload",
    "ToolCall",
    "ToolCallRequestPayload",
    "ToolCallResultPayload",
    "ToolCallStartPayload",
    "ToolErrorPayload",
    "ToolSchema",
    "Usage",
    "UserInterruptPayload",
    "UserMessageReceivedPayload",
    "new_event",
]
