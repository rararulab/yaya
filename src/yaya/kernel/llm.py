"""LLM-provider contract v1: streaming, TokenUsage, typed errors.

This module is the **authoritative** Python surface of yaya's
``llm-provider`` contract — the mirror of the "LLM providers (v1
contract)" section in ``docs/dev/plugin-protocol.md``. Providers
implement the :class:`LLMProvider` Protocol; the kernel wires them to
``llm.call.request`` events and consumes a :class:`StreamedMessage`
that yields :class:`StreamPart` chunks and carries a final
:class:`TokenUsage`.

The design deliberately mirrors the discipline kimi-cli uses in its
``kosong.chat_provider`` subpackage: streaming first, a token-usage
model that captures Anthropic cache accounting for free, a closed
error taxonomy, and SDK-specific converters that translate vendor
exceptions into that taxonomy.

**SDK-only rule (non-negotiable).** LLM-provider plugins MUST use the
official ``openai`` or ``anthropic`` Python SDK — nothing else. Raw
``httpx``, community wrappers, LangChain-style framework SDKs, and any
other agent framework are banned. Mechanical enforcement lives in
``scripts/check_banned_frameworks.py`` (the
:func:`check_llm_plugin_imports` rule). The converters here import the
SDKs *lazily* so a missing install does not crash the kernel at import
time — they degrade to a generic :class:`ChatProviderError`.

Layering: this module lives in ``src/yaya/kernel/`` and must not import
from ``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import (
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

from pydantic import BaseModel, Field, model_serializer

from yaya.kernel.errors import YayaError

__all__ = [
    "APIConnectionError",
    "APIEmptyResponseError",
    "APIStatusError",
    "APITimeoutError",
    "ChatProviderError",
    "ContentPart",
    "LLMProvider",
    "RetryableChatProvider",
    "StreamPart",
    "StreamedMessage",
    "ThinkingEffort",
    "TokenUsage",
    "ToolCallPart",
    "anthropic_to_chat_provider_error",
    "convert_httpx_error",
    "openai_to_chat_provider_error",
]


# ---------------------------------------------------------------------------
# Thinking effort.
# ---------------------------------------------------------------------------

ThinkingEffort = Literal["off", "low", "medium", "high"]
"""Reasoning / extended-thinking budget hint; provider-translated."""


# ---------------------------------------------------------------------------
# TokenUsage — mirrors kimi-cli so Anthropic cache accounting is free.
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token accounting carried on the terminal LLM response.

    Shape matches ``kosong.chat_provider.TokenUsage``: the four raw
    counters split input tokens into *fresh* (``input_other``), *cache
    hit* (``input_cache_read``), and *cache creation*
    (``input_cache_creation``). That split is material for Anthropic —
    cache hits and cache writes bill differently — and collapses
    safely to ``input_other`` for providers without cache accounting.

    The derived ``input`` and ``total`` fields make call-site math
    ergonomic without giving up the raw split.
    """

    input_other: int = Field(default=0, ge=0)
    """Fresh input tokens (not served from or written to cache)."""

    output: int = Field(default=0, ge=0)
    """Output / completion tokens emitted by the model."""

    input_cache_read: int = Field(default=0, ge=0)
    """Anthropic: tokens served from the prompt cache."""

    input_cache_creation: int = Field(default=0, ge=0)
    """Anthropic: tokens written into the prompt cache on this turn."""

    @property
    def input(self) -> int:
        """Total input tokens = fresh + cache_read + cache_creation."""
        return self.input_other + self.input_cache_read + self.input_cache_creation

    @property
    def total(self) -> int:
        """All tokens billed for this call."""
        return self.input + self.output

    @model_serializer(mode="wrap")
    def _serialize_with_derived(
        self,
        handler: Any,
    ) -> dict[str, Any]:
        """Serialize raw counters plus the derived ``input`` / ``total``.

        Plain ``@property`` (rather than ``@computed_field``) keeps the
        type-checker story symmetric across mypy and pyright — pydantic's
        ``@computed_field`` stacked on ``@property`` requires asymmetric
        ignores (mypy fires ``prop-decorator``; pyright does not). A
        ``model_serializer`` wrap delivers the same JSON shape without
        that ergonomic tax.
        """
        data: dict[str, Any] = handler(self)
        data["input"] = self.input
        data["total"] = self.total
        return data


# ---------------------------------------------------------------------------
# Stream parts.
# ---------------------------------------------------------------------------


class ContentPart(BaseModel):
    """One content chunk in a streamed LLM response."""

    type: Literal["content"] = "content"
    text: str


class ToolCallPart(BaseModel):
    """One terminal tool-call chunk in a streamed LLM response.

    Providers may also emit partial tool-call deltas — those are
    modelled as plain dicts on the wire under
    :class:`~yaya.kernel.events.LlmCallDeltaPayload`'s
    ``tool_call_partial`` key. ``ToolCallPart`` is reserved for the
    terminal, fully-assembled tool-call the loop forwards to tools.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


StreamPart = ContentPart | ToolCallPart
"""Union of stream-part kinds a ``StreamedMessage`` yields."""


# ---------------------------------------------------------------------------
# Protocols.
# ---------------------------------------------------------------------------


@runtime_checkable
class StreamedMessage(Protocol):
    """An async iterator over :data:`StreamPart` with a terminal usage.

    Providers return one of these from :meth:`LLMProvider.generate`.
    The agent loop iterates parts, re-emits them as
    ``llm.call.delta`` events, and — once iteration completes — reads
    :attr:`usage` for the final ``llm.call.response`` payload.

    ``id`` is the provider's message id (OpenAI: ``chatcmpl-...``,
    Anthropic: ``msg_...``). When the upstream SDK does not expose
    one, providers synthesize a stable opaque id so event correlation
    downstream has something to key on.
    """

    id: str

    def __aiter__(self) -> AsyncIterator[StreamPart]:
        """Async-iterate stream parts in emission order."""
        ...

    @property
    def usage(self) -> TokenUsage:
        """Terminal token usage; only valid after the stream completes."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """The v1 llm-provider contract.

    Providers are stateful long-lived objects: they own an SDK client,
    credentials, and per-process connection pools. That is why this is
    a :class:`~typing.Protocol` (not a pydantic ``BaseModel`` like the
    tool contract) — providers are not request objects.

    ``name`` identifies the provider on the bus (``ev.payload["provider"]``
    on ``llm.call.request`` filters against it). ``model_name`` is the
    default model the provider calls when a request omits ``model``.
    ``thinking_effort`` is a provider-interpreted reasoning budget; the
    kernel forwards it unchanged.

    :meth:`generate` is the single entry point. It MUST return a
    :class:`StreamedMessage`; synchronous / buffered completions are
    modelled as a one-part stream.
    """

    name: str
    model_name: str
    thinking_effort: ThinkingEffort

    async def generate(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> StreamedMessage:
        """Start a completion and return an async-iterable stream.

        Args:
            system_prompt: The provider-neutral system prompt. Providers
                translate it into the vendor shape (OpenAI: role=system;
                Anthropic: ``system=`` kwarg).
            tools: OpenAI-function-call-shape tool specs (``{"name":
                str, "description": str, "parameters": <json_schema>}``)
                — the same shape produced by
                :meth:`yaya.kernel.tool.Tool.openai_function_spec`.
                Anthropic providers translate to ``input_schema``.
            history: Chat history in the vendor-neutral
                ``{role, content}`` shape (see
                :class:`yaya.kernel.events.Message`).

        Returns:
            A :class:`StreamedMessage` the caller iterates.

        Raises:
            ChatProviderError: When the provider cannot start the
                stream — all subclasses represent typed failures
                callers (and retry policies) can switch on. Raw SDK
                exceptions MUST be translated with the converters
                below before they escape the plugin boundary.
        """
        ...


@runtime_checkable
class RetryableChatProvider(Protocol):
    """Opt-in retry hook for providers that want loop-driven retries.

    The kernel's retry runtime (follow-up PR) calls
    :meth:`on_retryable_error` between attempts when the last error is
    a :class:`ChatProviderError` and the provider advertises this
    Protocol. Returning ``True`` schedules another attempt; ``False``
    propagates the error.

    Providers opt in simply by implementing the method — no
    registration needed. The shape is frozen here; the runtime that
    consumes it lands separately so the contract is stable before any
    provider relies on it.
    """

    async def on_retryable_error(
        self,
        exc: ChatProviderError,
        attempt: int,
    ) -> bool:
        """Decide whether to retry after a typed provider error.

        Args:
            exc: The error raised on the previous attempt.
            attempt: 1-indexed attempt count that just failed.

        Returns:
            ``True`` to retry; ``False`` to give up and surface ``exc``.
        """
        ...


# ---------------------------------------------------------------------------
# Error taxonomy.
# ---------------------------------------------------------------------------


class ChatProviderError(YayaError):
    """Base class for every llm-provider failure.

    Subclasses this module defines are the closed set; plugins should
    not invent new subclasses (instead, choose the best-fit existing
    one and carry detail in the message). All subclass
    :class:`~yaya.kernel.errors.YayaError` so existing
    ``except YayaError:`` handlers still catch provider failures.
    """

    def __init__(
        self,
        message: str = "",
        *,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id


class APIConnectionError(ChatProviderError):
    """Transport-level failure — connection refused / DNS / reset.

    Retry-safe: the request did not reach the provider, or the
    response did not complete. Loop-level retry policies treat this as
    a plain "retry with backoff".
    """


class APITimeoutError(ChatProviderError):
    """Client or server-side timeout waiting for a response.

    Retry-safe in the same sense as :class:`APIConnectionError`: the
    request may or may not have been accepted, but the stream never
    delivered. Providers that care about idempotency should carry a
    per-attempt request id themselves.
    """


class APIStatusError(ChatProviderError):
    """Provider returned a non-2xx status.

    Carries ``status_code`` (int) and optional ``request_id`` (str,
    from ``x-request-id`` / equivalent). Callers distinguish retry
    semantics by status: 408 / 429 / 5xx are usually retry-safe; 400 /
    401 / 403 / 404 are not.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message, request_id=request_id)
        self.status_code = status_code


class APIEmptyResponseError(ChatProviderError):
    """Stream closed with no content and no tool-calls.

    Typically a truncated upstream response. Surfaced as a typed error
    rather than a silent empty ``llm.call.response`` so adapters can
    render an actionable message instead of a blank turn.
    """


# ---------------------------------------------------------------------------
# SDK error converters (lazy imports so a missing SDK does not break boot).
# ---------------------------------------------------------------------------


def openai_to_chat_provider_error(exc: BaseException) -> ChatProviderError:
    """Translate an ``openai`` SDK exception into the yaya taxonomy.

    Accepts ``BaseException`` (not the SDK type) so callers do not
    have to gate the import on SDK presence. If the ``openai`` SDK is
    not installed, the function falls back to a generic
    :class:`ChatProviderError` — the plugin loader would have
    surfaced the missing dependency earlier in :meth:`Plugin.on_load`.

    Args:
        exc: The raised exception. Usually an ``openai.OpenAIError``
            subclass; non-SDK exceptions collapse to a generic
            :class:`ChatProviderError` carrying ``str(exc)``.

    Returns:
        A :class:`ChatProviderError` subclass mirroring the SDK's
        error kind.
    """
    try:
        import openai as _openai
    except ImportError:
        return ChatProviderError(str(exc) or type(exc).__name__)

    request_id = _safe_attr(exc, "request_id")
    message = str(exc) or type(exc).__name__

    # Ordering: Timeout before Connection because APITimeoutError is a
    # subclass of APIConnectionError in some SDK versions; we want the
    # more specific mapping to win.
    api_timeout = getattr(_openai, "APITimeoutError", None)
    if api_timeout is not None and isinstance(exc, api_timeout):
        return APITimeoutError(message, request_id=request_id)

    api_connection = getattr(_openai, "APIConnectionError", None)
    if api_connection is not None and isinstance(exc, api_connection):
        return APIConnectionError(message, request_id=request_id)

    api_status = getattr(_openai, "APIStatusError", None)
    if api_status is not None and isinstance(exc, api_status):
        status_code = int(_safe_attr(exc, "status_code") or 0)
        return APIStatusError(
            message,
            status_code=status_code,
            request_id=request_id,
        )

    return ChatProviderError(message, request_id=request_id)


def anthropic_to_chat_provider_error(exc: BaseException) -> ChatProviderError:
    """Translate an ``anthropic`` SDK exception into the yaya taxonomy.

    Mirrors :func:`openai_to_chat_provider_error` for the
    ``anthropic`` SDK (same class names — the two SDKs converged on
    the taxonomy). Lazy import; degrades to a generic
    :class:`ChatProviderError` when the SDK is missing.
    """
    try:
        # Soft dependency: the ``anthropic`` SDK is currently pulled in as a
        # transitive of ``republic`` (issue #32). The import stays guarded so
        # the kernel keeps booting if a future republic release drops it.
        import anthropic as _anthropic
    except ImportError:
        return ChatProviderError(str(exc) or type(exc).__name__)

    request_id = _safe_attr(exc, "request_id")
    message = str(exc) or type(exc).__name__

    api_timeout = getattr(_anthropic, "APITimeoutError", None)
    if api_timeout is not None and isinstance(exc, api_timeout):
        return APITimeoutError(message, request_id=request_id)

    api_connection = getattr(_anthropic, "APIConnectionError", None)
    if api_connection is not None and isinstance(exc, api_connection):
        return APIConnectionError(message, request_id=request_id)

    api_status = getattr(_anthropic, "APIStatusError", None)
    if api_status is not None and isinstance(exc, api_status):
        status_code = int(_safe_attr(exc, "status_code") or 0)
        return APIStatusError(
            message,
            status_code=status_code,
            request_id=request_id,
        )

    return ChatProviderError(message, request_id=request_id)


def convert_httpx_error(exc: BaseException) -> ChatProviderError:
    """Translate a raw ``httpx`` error that leaked through an SDK stream.

    Kimi-cli precedent: both the ``openai`` and ``anthropic`` async
    SDKs occasionally let ``httpx.HTTPError`` subclasses escape the
    SDK envelope during streaming (connection reset mid-chunk, read
    timeout). This converter gives plugins a single place to funnel
    those through into the yaya taxonomy.

    Lazy import so tests / environments without ``httpx`` still
    function — unknown exception types degrade to a generic
    :class:`ChatProviderError`.
    """
    try:
        import httpx as _httpx
    except ImportError:
        return ChatProviderError(str(exc) or type(exc).__name__)

    message = str(exc) or type(exc).__name__

    if isinstance(exc, _httpx.TimeoutException):
        return APITimeoutError(message)

    if isinstance(exc, _httpx.ConnectError):
        return APIConnectionError(message)

    if isinstance(exc, _httpx.HTTPStatusError):
        status_code = int(_safe_attr(getattr(exc, "response", None), "status_code") or 0)
        return APIStatusError(message, status_code=status_code)

    if isinstance(exc, _httpx.HTTPError):
        return APIConnectionError(message)

    return ChatProviderError(message)


def _safe_attr(obj: object, name: str) -> Any:
    """Return ``obj.name`` if present, else ``None``; never raises."""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None
