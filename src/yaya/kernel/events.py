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
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, get_args

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
    "llm.call.response",
    "llm.call.error",
    # Tool execution (kernel ↔ tool).
    "tool.call.request",
    "tool.call.start",
    "tool.call.result",
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


class UserMessageReceivedPayload(TypedDict, total=False):
    """``user.message.received`` — text the adapter just received from the user."""

    text: str
    attachments: list[Attachment]


class UserInterruptPayload(TypedDict, total=False):
    """``user.interrupt`` — user-requested end-of-turn; payload is empty by design."""


# --- Assistant output ------------------------------------------------------


class AssistantMessageDeltaPayload(TypedDict):
    """``assistant.message.delta`` — one streaming chunk of assistant content."""

    content: str


class AssistantMessageDonePayload(TypedDict, total=False):
    """``assistant.message.done`` — terminal assistant message for a turn."""

    content: str
    tool_calls: list[ToolCall]


# --- LLM invocation --------------------------------------------------------


class LlmCallRequestPayload(TypedDict, total=False):
    """``llm.call.request`` — kernel asks a provider plugin to run a completion."""

    provider: str
    model: str
    messages: list[Message]
    tools: list[ToolSchema]
    params: dict[str, Any]


class LlmCallResponsePayload(TypedDict, total=False):
    """``llm.call.response`` — provider's completion result."""

    text: str
    tool_calls: list[ToolCall]
    usage: Usage


class LlmCallErrorPayload(TypedDict, total=False):
    """``llm.call.error`` — provider failure with optional retry hint."""

    error: str
    retry_after_s: float


# --- Tool execution --------------------------------------------------------


class ToolCallRequestPayload(TypedDict):
    """``tool.call.request`` — kernel asks a tool plugin to run."""

    id: str
    name: str
    args: dict[str, Any]


class ToolCallStartPayload(TypedDict):
    """``tool.call.start`` — broadcast to adapters so the UI can render progress."""

    id: str
    name: str
    args: dict[str, Any]


class ToolCallResultPayload(TypedDict, total=False):
    """``tool.call.result`` — tool plugin's outcome."""

    id: str
    ok: bool
    value: Any
    error: str


# --- Memory ----------------------------------------------------------------


class MemoryQueryPayload(TypedDict):
    """``memory.query`` — kernel asks a memory plugin for ``k`` relevant entries."""

    query: str
    k: int


class MemoryWritePayload(TypedDict):
    """``memory.write`` — kernel asks a memory plugin to persist one entry."""

    entry: MemoryEntry


class MemoryResultPayload(TypedDict):
    """``memory.result`` — memory plugin's hits list."""

    hits: list[MemoryEntry]


# --- Strategy --------------------------------------------------------------


class StrategyDecideRequestPayload(TypedDict):
    """``strategy.decide.request`` — kernel asks the active strategy for a next step."""

    state: AgentLoopState


class StrategyDecideResponsePayload(TypedDict, total=False):
    """``strategy.decide.response`` — strategy's chosen next step.

    ``next`` is one of ``"llm" | "tool" | "memory" | "done"``; additional keys
    describe the arguments for that step (kept as an open dict at the kernel
    level — strategy plugins own their own schema).
    """

    next: Literal["llm", "tool", "memory", "done"]


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
    """

    name: str
    error: str


# --- Kernel ----------------------------------------------------------------


class KernelReadyPayload(TypedDict):
    """``kernel.ready`` — kernel boot finished, plugins loaded."""

    version: str


class KernelShutdownPayload(TypedDict):
    """``kernel.shutdown`` — kernel is stopping; adapters should drain."""

    reason: str


class KernelErrorPayload(TypedDict):
    """``kernel.error`` — the kernel itself failed; ``yaya serve`` exits non-zero."""

    source: str
    message: str


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
    payload: dict[str, Any] = field(default_factory=dict)


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
    "ToolSchema",
    "Usage",
    "UserInterruptPayload",
    "UserMessageReceivedPayload",
    "new_event",
]
