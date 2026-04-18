# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""OpenAI LLM-provider plugin implementation.

The plugin is env-driven: ``OPENAI_API_KEY`` is required (a missing
key flips the plugin to an unconfigured state that surfaces
``llm.call.error`` per request rather than crashing the kernel) and
``OPENAI_BASE_URL`` is optional (passed straight through to the SDK
for self-hosted / proxy deployments).

Non-streaming chat completions only at 0.1; streaming is tracked in
the adapter-plugin spec. Every response event echoes ``request_id``
so the agent loop's ``_RequestTracker`` correlates concurrent calls
(lesson #15 in ``docs/wiki/lessons-learned.md``).

The file-level ``pyright: reportUnknown*=false`` pragmas are necessary
because the OpenAI SDK's return types resolve to ``Unknown`` in
environments where pyright does not discover the project virtualenv
(notably the CI linter step). The runtime shape is covered by
``tests/plugins/llm_openai/`` using a stubbed ``AsyncOpenAI`` client,
and every outbound ``dict[str, Any]`` is constructed from typed
locals so the contract at the bus boundary stays checked.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

if TYPE_CHECKING:  # pragma: no cover - type-only import.
    from openai import AsyncOpenAI

_NAME = "llm-openai"
_VERSION = "0.1.0"
_PROVIDER_ID = "openai"


class OpenAIProvider:
    """Bundled OpenAI LLM-provider plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.LLM_PROVIDER`.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.LLM_PROVIDER
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._configured: bool = False

    def subscriptions(self) -> list[str]:
        """Only ``llm.call.request`` — the single request kind for this category."""
        return ["llm.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Read env, build an ``AsyncOpenAI`` client, or log + degrade.

        A missing ``OPENAI_API_KEY`` is NOT a hard failure — the
        kernel keeps loading so users with other providers installed
        still get a working bus. Every incoming ``llm.call.request``
        for ``provider == "openai"`` then emits ``llm.call.error``.
        """
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL")
        if not api_key:
            ctx.logger.warning("llm-openai: OPENAI_API_KEY not set; calls will error")
            self._configured = False
            return
        # Import lazily so a missing openai install surfaces here, not at
        # kernel boot for users who do not use this plugin.
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._configured = True
        ctx.logger.debug("llm-openai loaded (base_url=%s)", base_url or "<default>")

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route ``llm.call.request`` for ``provider == "openai"``.

        Non-matching providers are ignored so sibling LLM plugins can
        coexist under the same bus subscription.
        """
        if ev.kind != "llm.call.request":
            return
        if ev.payload.get("provider") != _PROVIDER_ID:
            return

        if not self._configured or self._client is None:
            await ctx.emit(
                "llm.call.error",
                {"error": "not_configured", "request_id": ev.id},
                session_id=ev.session_id,
            )
            return

        try:
            await self._dispatch(ev, ctx)
        except asyncio.CancelledError:
            # Propagate cancellation — lesson #3.
            raise
        except Exception as exc:
            await self._emit_error(ctx, ev, exc)

    async def on_unload(self, ctx: KernelContext) -> None:
        """Best-effort ``AsyncOpenAI.close()``; never re-raise cleanup errors."""
        client, self._client = self._client, None
        self._configured = False
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:
            ctx.logger.warning("llm-openai: client close failed: %s", exc)

    # -- internals ------------------------------------------------------------

    async def _dispatch(self, ev: Event, ctx: KernelContext) -> None:
        """Invoke chat completions and emit ``llm.call.response``."""
        assert self._client is not None  # noqa: S101 - guarded by on_event.
        payload = ev.payload
        model = str(payload.get("model", ""))
        messages = cast("list[dict[str, Any]]", payload.get("messages") or [])
        tools = payload.get("tools")

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools

        # The SDK typing union (ChatCompletion | AsyncStream[...]) widens
        # everything past this point to Any — that matches the tests'
        # stub-client pattern and the runtime shape we actually emit.
        completion: Any = await self._client.chat.completions.create(**create_kwargs)

        choices_raw: Any = completion.choices or []
        choices: list[Any] = list(choices_raw) if choices_raw else []
        choice: Any = choices[0] if choices else None
        message: Any = choice.message if choice is not None else None
        text: str = getattr(message, "content", "") or ""
        raw_tool_calls_obj: Any = getattr(message, "tool_calls", None) or []
        raw_tool_calls: list[Any] = list(raw_tool_calls_obj) if raw_tool_calls_obj else []
        tool_calls = [_tool_call_to_dict(tc) for tc in raw_tool_calls]

        usage_obj: Any = getattr(completion, "usage", None)
        usage: dict[str, int] = {}
        if usage_obj is not None:
            input_tokens = getattr(usage_obj, "prompt_tokens", None)
            output_tokens = getattr(usage_obj, "completion_tokens", None)
            if input_tokens is not None:
                usage["input_tokens"] = int(input_tokens)
            if output_tokens is not None:
                usage["output_tokens"] = int(output_tokens)

        await ctx.emit(
            "llm.call.response",
            {
                "text": text,
                "tool_calls": tool_calls,
                "usage": usage,
                "request_id": ev.id,
            },
            session_id=ev.session_id,
        )

    async def _emit_error(
        self,
        ctx: KernelContext,
        ev: Event,
        exc: BaseException,
    ) -> None:
        """Translate SDK errors into ``llm.call.error`` payloads."""
        # Import lazily inside the error path so we only depend on the SDK
        # types when an error actually happens.
        payload: dict[str, Any] = {"error": str(exc), "request_id": ev.id}
        try:
            from openai import RateLimitError

            if isinstance(exc, RateLimitError):
                retry_after = _extract_retry_after(exc)
                if retry_after is not None:
                    payload["retry_after_s"] = retry_after
        except ImportError:
            # SDK missing — ``on_load`` would have logged; just emit the error.
            pass
        await ctx.emit(
            "llm.call.error",
            payload,
            session_id=ev.session_id,
        )


def _tool_call_to_dict(tc: Any) -> dict[str, Any]:
    """Normalize an SDK tool-call object to the kernel's ``ToolCall`` shape."""
    # The SDK exposes either a pydantic model (with ``model_dump``) or a
    # dict already in tests. Try ``model_dump`` first, fall back to dict().
    dump: Any = getattr(tc, "model_dump", None)
    if callable(dump):
        data: Any = dump()
        if isinstance(data, dict):
            return cast("dict[str, Any]", data)
    if isinstance(tc, dict):
        return cast("dict[str, Any]", tc)
    return {
        "id": getattr(tc, "id", ""),
        "name": getattr(getattr(tc, "function", None), "name", ""),
        "args": {},
    }


def _extract_retry_after(exc: BaseException) -> float | None:
    """Pull a ``retry_after`` hint off a ``RateLimitError`` if the SDK exposed one."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


__all__ = ["OpenAIProvider"]
