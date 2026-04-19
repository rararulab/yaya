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

Per-line ``# pyright: ignore[...]`` pragmas on the SDK-accessor lines
narrow the suppression to exactly where the OpenAI SDK surface widens
to ``Unknown`` (see lesson #21). The runtime shape is covered by
``tests/plugins/llm_openai/`` using a stubbed ``AsyncOpenAI`` client.
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
        """Subscribe to llm requests and live-config updates.

        ``config.updated`` drives the hot-reload path: when a
        ``plugin.llm_openai.*`` key changes (api_key, base_url) the
        plugin rebuilds its :class:`AsyncOpenAI` client without a
        kernel restart. Non-matching prefixes are filtered in
        :meth:`on_event` so the bus's exact-kind routing stays cheap.
        """
        return ["llm.call.request", "config.updated"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Build an ``AsyncOpenAI`` client from live config + env.

        A missing ``OPENAI_API_KEY`` is NOT a hard failure — the
        kernel keeps loading so users with other providers installed
        still get a working bus. Every incoming ``llm.call.request``
        for ``provider == "openai"`` then emits ``llm.call.error``.

        Live config (``ctx.config["api_key"]`` / ``["base_url"]``)
        wins over the ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` env
        vars. The env fallback keeps zero-config `yaya serve` working
        for developers before they wire a ConfigStore value.
        """
        await self._rebuild_client(ctx)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route ``llm.call.request`` for ``provider == "openai"``.

        ``config.updated`` triggers a client rebuild when the touched
        key lives under this plugin's namespace; other prefixes are
        ignored. Non-matching providers on ``llm.call.request`` are
        ignored so sibling LLM plugins can coexist under the same
        bus subscription.
        """
        if ev.kind == "config.updated":
            await self._maybe_hot_reload(ev, ctx)
            return
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

    async def _rebuild_client(self, ctx: KernelContext) -> None:
        """(Re)build the ``AsyncOpenAI`` client from live config + env.

        Closes a previous client (if any) before opening a new one so
        a hot-reload swap does not leak a connection pool. Live
        ``ConfigStore`` values win over the legacy env vars; the env
        fallback keeps zero-config boots working.
        """
        cfg = ctx.config
        cfg_api_key = cfg.get("api_key") if cfg else None
        cfg_base_url = cfg.get("base_url") if cfg else None

        api_key = cfg_api_key if isinstance(cfg_api_key, str) and cfg_api_key else os.environ.get("OPENAI_API_KEY")
        base_url_val = (
            cfg_base_url if isinstance(cfg_base_url, str) and cfg_base_url else os.environ.get("OPENAI_BASE_URL")
        )

        # Tear down any live client before swapping so an old pool does
        # not outlive the config it was built from.
        existing, self._client = self._client, None
        self._configured = False
        if existing is not None:
            try:
                await existing.close()
            except Exception as exc:
                ctx.logger.warning("llm-openai: stale client close failed: %s", exc)

        if not api_key:
            ctx.logger.warning("llm-openai: OPENAI_API_KEY not set; calls will error")
            return
        # Import lazily so a missing openai install surfaces here, not at
        # kernel boot for users who do not use this plugin.
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url_val:
            kwargs["base_url"] = base_url_val
        self._client = AsyncOpenAI(**kwargs)
        self._configured = True
        ctx.logger.debug("llm-openai client built (base_url=%s)", base_url_val or "<default>")

    async def _maybe_hot_reload(self, ev: Event, ctx: KernelContext) -> None:
        """Rebuild the client when a ``plugin.llm_openai.*`` key changed.

        The scoped :class:`~yaya.kernel.config_store.ConfigView` handed
        to this plugin already reflects the new value by the time this
        handler runs (the store updates its cache before emitting);
        this method just forces a client re-spin for keys that affect
        connection identity (``api_key``, ``base_url``).
        """
        key_raw = ev.payload.get("key")
        if not isinstance(key_raw, str):
            return
        # The registry scopes each plugin's config view to
        # ``plugin.llm_openai.`` — hot-reload when the touched key
        # lives inside that namespace.
        if not key_raw.startswith("plugin.llm_openai."):
            return
        suffix = key_raw[len("plugin.llm_openai.") :]
        if suffix not in {"api_key", "base_url"}:
            return
        await self._rebuild_client(ctx)

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
        completion: Any = await self._client.chat.completions.create(**create_kwargs)  # pyright: ignore[reportUnknownVariableType]

        choices_raw: Any = completion.choices or []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        choices: list[Any] = list(choices_raw) if choices_raw else []  # pyright: ignore[reportUnknownArgumentType]
        choice: Any = choices[0] if choices else None
        message: Any = choice.message if choice is not None else None
        text: str = getattr(message, "content", "") or ""
        raw_tool_calls_obj: Any = getattr(message, "tool_calls", None) or []
        raw_tool_calls: list[Any] = list(raw_tool_calls_obj) if raw_tool_calls_obj else []
        tool_calls = [_tool_call_to_dict(tc) for tc in raw_tool_calls]

        usage_obj: Any = getattr(completion, "usage", None)  # pyright: ignore[reportUnknownArgumentType]
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
