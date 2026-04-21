"""OpenAI LLM-provider plugin implementation.

The plugin is **instance-scoped**: after #123 (D4b) it maintains one
:class:`openai.AsyncOpenAI` client per configured ``providers.<id>.*``
row whose ``plugin`` meta equals ``llm-openai``. Dispatch happens by
matching ``ev.payload["provider"]`` (an *instance id*, not the plugin
name) against the plugin's owned-client dict. One plugin process
therefore backs many operator-configured instances — e.g. "OpenAI
prod", "Azure OpenAI", "local-LM-Studio" — each with its own
``api_key``, ``base_url``, and ``model`` fields.

Non-streaming chat completions only at 0.1; streaming is tracked in
the adapter-plugin spec. Every response event echoes ``request_id``
so the agent loop's ``_RequestTracker`` correlates concurrent calls
(lesson #15 in ``docs/wiki/lessons-learned.md``).

Hot-reload is per-instance: a ``config.updated`` event whose key sits
under ``providers.<id>.`` rebuilds *only* that instance's client.
Adding a new instance (``providers.<id>.plugin = llm-openai``)
materialises a new client; removing / re-pointing drops the old one.
Zero plugin restart in any of these paths.

Per-line ``# pyright: ignore[...]`` pragmas on the SDK-accessor lines
narrow the suppression to exactly where the OpenAI SDK surface widens
to ``Unknown`` (see lesson #21). The runtime shape is covered by
``tests/plugins/llm_openai/`` using a stubbed ``AsyncOpenAI`` client.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

if TYPE_CHECKING:  # pragma: no cover - type-only import.
    from openai import AsyncOpenAI

    from yaya.kernel.providers import InstanceRow

_NAME = "llm-openai"
_VERSION = "0.1.0"
_PROVIDERS_PREFIX = "providers."
_DEFAULT_MODEL = "gpt-4o-mini"


class _OpenAIInstanceConfig(BaseModel):
    """Instance-scoped config schema for one ``llm-openai`` provider.

    Surfaced through ``GET /api/llm-providers`` so the web settings UI
    can render a typed configure form (api_key as a masked password
    field, base_url + model as plain text). Falls back to
    ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` env vars when fields are
    blank — see :meth:`OpenAIProvider._build_client`.
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(
        default=None,
        description="OpenAI API key. Falls back to the OPENAI_API_KEY env var.",
        json_schema_extra={"format": "password"},
    )
    base_url: str | None = Field(
        default=None,
        description="Custom API base URL (e.g. Azure OpenAI, LM Studio, vLLM). "
        "Falls back to the OPENAI_BASE_URL env var, then the OpenAI default.",
    )
    model: str | None = Field(
        default=None,
        description=f"Default model name. Defaults to {_DEFAULT_MODEL} when unset.",
    )


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
    # Picked up by the web adapter's `_plugin_config_schema` helper to
    # drive the configure form in Settings → LLM Providers.
    ConfigModel: ClassVar[type[BaseModel]] = _OpenAIInstanceConfig

    def __init__(self) -> None:
        # Instance-id → live ``AsyncOpenAI`` client. Populated in
        # :meth:`on_load` from every ``providers.<id>.*`` row whose
        # ``plugin`` meta equals this plugin's name, and mutated
        # incrementally by :meth:`_maybe_hot_reload` as operators
        # edit config.
        self._clients: dict[str, AsyncOpenAI] = {}

    def subscriptions(self) -> list[str]:
        """Subscribe to llm requests and live-config updates.

        ``config.updated`` drives the hot-reload path: when a
        ``providers.<id>.*`` key changes the plugin rebuilds the
        affected instance's :class:`AsyncOpenAI` client without a
        kernel restart. Non-matching prefixes are filtered in
        :meth:`on_event` so the bus's exact-kind routing stays cheap.
        """
        return ["llm.call.request", "config.updated"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Build one :class:`AsyncOpenAI` client per owned instance.

        Reads every ``providers.<id>.*`` row whose ``plugin`` meta
        equals :data:`_NAME` via :attr:`ctx.providers` and seeds
        :attr:`_clients`. Instances with no ``api_key`` (and no
        ``OPENAI_API_KEY`` env fallback) are skipped with a WARNING
        rather than hard-failing, so a partially-configured install
        still boots and surfaces "no subscriber" on requests naming
        them.

        If ``ctx.providers`` is ``None`` (kernel booted without a
        config store — tests, transient stacks) the plugin loads in
        an empty state; tests that manually set ``self._clients``
        then run through the dispatch path unchanged.

        One-shot legacy-config warning: if a ``plugin.llm_openai.*``
        key is still present AND no owned instance exists yet, log
        a hint pointing operators at the new namespace. Does NOT
        auto-lift — that is D4a's job (registry bootstrap) and
        re-doing it here would mask bootstrap regressions.
        """
        providers = ctx.providers
        self._clients = {}
        if providers is None:
            return
        for inst in providers.instances_for_plugin(_NAME):
            client = self._build_client(inst, ctx)
            if client is not None:
                self._clients[inst.id] = client
        if not self._clients:
            self._warn_legacy_keys(ctx)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route ``llm.call.request`` to the matching instance client.

        ``ev.payload["provider"]`` is an *instance id* (D4b); the
        plugin answers only when that id names one of its owned
        clients. ``config.updated`` triggers per-instance rebuilds.
        Non-matching providers on ``llm.call.request`` are ignored so
        sibling LLM plugins coexist under the same bus subscription.
        """
        if ev.kind == "config.updated":
            await self._maybe_hot_reload(ev, ctx)
            return
        if ev.kind != "llm.call.request":
            return
        provider_id = ev.payload.get("provider")
        if not isinstance(provider_id, str) or provider_id not in self._clients:
            return

        try:
            await self._dispatch(ev, ctx, provider_id)
        except asyncio.CancelledError:
            # Propagate cancellation — lesson #3.
            raise
        except Exception as exc:
            await self._emit_error(ctx, ev, exc)

    async def on_unload(self, ctx: KernelContext) -> None:
        """Drop every owned client; never re-raise cleanup errors.

        Mirrors the rebuild path's leak policy: ``AsyncOpenAI``
        reclaims its ``httpx`` pool on GC, so we do *not* await
        ``close()`` (which would tear pools out from under any
        in-flight ``_dispatch``). See :meth:`_rebuild_instance` for
        the matching rationale.
        """
        self._clients.clear()
        del ctx  # unused — kept for Plugin protocol conformance.

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _build_client(inst: InstanceRow, ctx: KernelContext) -> AsyncOpenAI | None:
        """Construct one :class:`AsyncOpenAI` client from an instance row.

        Instance ``config["api_key"]`` wins over the ``OPENAI_API_KEY``
        env var; missing both is not a hard failure — the instance is
        simply absent from :attr:`_clients` and requests naming it
        fall through to the bus's "no subscriber" path (silent). The
        operator sees the WARNING in the plugin log.
        """
        api_key_raw = inst.config.get("api_key")
        base_url_raw = inst.config.get("base_url")
        api_key = api_key_raw if isinstance(api_key_raw, str) and api_key_raw else os.environ.get("OPENAI_API_KEY")
        base_url = base_url_raw if isinstance(base_url_raw, str) and base_url_raw else os.environ.get("OPENAI_BASE_URL")
        if not api_key:
            ctx.logger.warning(
                "llm-openai: instance %r has no api_key (and OPENAI_API_KEY unset); skipping",
                inst.id,
            )
            return None
        # Import lazily so a missing openai install surfaces here, not
        # at kernel boot for users who do not use this plugin.
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        ctx.logger.debug(
            "llm-openai: built client for instance %r (base_url=%s)",
            inst.id,
            base_url or "<default>",
        )
        return AsyncOpenAI(**kwargs)

    @staticmethod
    def _warn_legacy_keys(ctx: KernelContext) -> None:
        """Hint when config still uses ``plugin.llm_openai.*`` with no instance.

        The D4a registry bootstrap already lifts those rows into
        ``providers.llm-openai.*`` on first boot; a non-empty
        ``plugin.llm_openai.*`` subtree *without* a matching provider
        instance means either the bootstrap did not run (pre-D4a
        database) or an operator is still setting keys via the legacy
        namespace. Log once at WARNING so it is visible without
        crashing the kernel.
        """
        store = ctx.config_store
        if store is None:
            return
        cache = store._cache  # pyright: ignore[reportPrivateUsage]
        legacy_prefix = "plugin.llm_openai."
        if any(k.startswith(legacy_prefix) for k in cache):
            ctx.logger.warning(
                "llm-openai: legacy plugin.llm_openai.* keys present but no providers.<id>.plugin=llm-openai "
                "instance is configured; use `yaya config set providers.llm-openai.api_key ...` "
                "(D4c CRUD lands next)"
            )

    async def _maybe_hot_reload(self, ev: Event, ctx: KernelContext) -> None:
        """React to a ``config.updated`` event scoped to ``providers.<id>.*``.

        The kernel's :class:`~yaya.kernel.config_store.ConfigStore`
        already updated its cache before publishing, so the
        :attr:`ctx.providers` view re-reads the fresh value. This
        method only decides *which* instance to rebuild — the
        per-client dict is the authoritative routing key, so changes
        to ``plugin`` meta (adding / removing this plugin's ownership)
        must insert or drop entries accordingly.
        """
        providers = ctx.providers
        if providers is None:
            return
        key_raw = ev.payload.get("key")
        if not isinstance(key_raw, str) or not key_raw.startswith(_PROVIDERS_PREFIX):
            return
        rest = key_raw[len(_PROVIDERS_PREFIX) :]
        if "." not in rest:
            return
        instance_id, _ = rest.split(".", 1)
        if not instance_id:
            return
        inst = providers.get_instance(instance_id)
        if inst is None:
            # Row removed entirely — drop any client we may have held.
            self._clients.pop(instance_id, None)
            return
        if inst.plugin != _NAME:
            # Instance either never belonged to us, or was just
            # re-pointed at a different plugin. Drop the client so
            # dispatch no longer answers for this id.
            self._clients.pop(instance_id, None)
            return
        # Owned by us → (re)build the client. ``model`` is read live
        # at dispatch time, so a ``model`` change needs no rebuild;
        # only fields that affect connection identity do. Rebuilding
        # unconditionally is the simpler correct path — the stale
        # client is dropped, not closed, to preserve in-flight calls.
        self._rebuild_instance(inst, ctx)

    def _rebuild_instance(self, inst: InstanceRow, ctx: KernelContext) -> None:
        """Swap the per-instance client without closing the old pool.

        Closing would tear down ``httpx`` out from under any
        ``_dispatch`` currently awaiting ``chat.completions.create``
        on the previous client (PR #105 follow-up #106, F1). The old
        client is released to GC instead; its pool is reclaimed
        asynchronously, bounded by rotations-per-process.
        """
        # Drop the reference first so a failing rebuild does not
        # leave stale state routing under the instance id.
        self._clients.pop(inst.id, None)
        client = self._build_client(inst, ctx)
        if client is not None:
            self._clients[inst.id] = client

    async def _dispatch(self, ev: Event, ctx: KernelContext, instance_id: str) -> None:
        """Invoke chat completions and emit ``llm.call.response``.

        Model selection is a live read against
        :attr:`ctx.providers` so ``yaya config set providers.<id>.model
        gpt-4.1`` takes effect on the next turn without a rebuild.
        """
        client = self._clients[instance_id]
        model = self._resolve_model(ctx, instance_id, ev.payload)
        payload = ev.payload
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
        completion: Any = await client.chat.completions.create(**create_kwargs)  # pyright: ignore[reportUnknownVariableType]

        choices_raw: Any = completion.choices or []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        choices: list[Any] = list(choices_raw) if choices_raw else []  # pyright: ignore[reportUnknownArgumentType]
        choice: Any = choices[0] if choices else None
        message: Any = choice.message if choice is not None else None
        raw_text: str = getattr(message, "content", "") or ""
        # Strip inline ``<think>...</think>`` reasoning blocks that
        # MiniMax / DeepSeek-R1 style models embed in ``content``.
        # Replaying those tags verbatim in the next
        # ``llm.call.request`` history leaves the model echoing only
        # more reasoning on turn 2 with no visible output — the chat
        # then looks single-round because ``assistant.message.done``
        # falls back to turn 1's thinking-only text (#149).
        text: str = _strip_reasoning_tags(raw_text)
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

    @staticmethod
    def _resolve_model(ctx: KernelContext, instance_id: str, payload: dict[str, Any]) -> str:
        """Resolve the model to request against this instance.

        Priority: instance config ``model`` → payload ``model`` →
        hard-coded :data:`_DEFAULT_MODEL`. The payload read keeps
        backwards-compat with agent loops that still thread the
        strategy's ``model`` decision through ``llm.call.request``.
        """
        providers = ctx.providers
        if providers is not None:
            inst = providers.get_instance(instance_id)
            if inst is not None:
                raw = inst.config.get("model")
                if isinstance(raw, str) and raw:
                    return raw
        payload_model = payload.get("model")
        if isinstance(payload_model, str) and payload_model:
            return payload_model
        return _DEFAULT_MODEL

    async def _emit_error(
        self,
        ctx: KernelContext,
        ev: Event,
        exc: BaseException,
    ) -> None:
        """Translate SDK errors into ``llm.call.error`` payloads."""
        # Import lazily inside the error path so we only depend on the
        # SDK types when an error actually happens.
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


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_reasoning_tags(text: str) -> str:
    """Remove inline ``<think>...</think>`` blocks from LLM content.

    MiniMax-M2, DeepSeek-R1, and related models stream their chain-
    of-thought inline as ``<think>...</think>`` inside the chat
    completion's ``content`` field. That text is useful for UI
    reasoning panels but toxic when replayed back into the next
    ``llm.call.request`` — the model sees its own thinking markers
    in history and stops producing visible output.

    Strip the tags here so every downstream consumer (loop replay,
    assistant.message.done, UI bubble) sees the post-reasoning text
    only. Preserving the reasoning for the UI is a later enhancement;
    the priority is unblocking the multi-turn conversation (#149).
    """
    if "<think>" not in text:
        return text
    return _THINK_RE.sub("", text).strip()


def _tool_call_to_dict(tc: Any) -> dict[str, Any]:
    """Normalize an SDK tool-call object to the kernel's ``ToolCall`` shape.

    The OpenAI SDK dumps ``{id, type, function: {name, arguments}}``
    where ``arguments`` is a JSON string. The kernel's
    :class:`~yaya.kernel.events.ToolCall` is flat — ``{id, name, args}``
    with ``args`` as a parsed ``dict``. This function bridges the two
    shapes so strategies and :meth:`AgentLoop._call_tool` see a
    uniform payload (#147). Dicts already in kernel shape (tests,
    legacy paths) fall through unchanged.
    """
    import json

    data: dict[str, Any] | None
    dump: Any = getattr(tc, "model_dump", None)
    if callable(dump):
        dumped: Any = dump()
        data = cast("dict[str, Any]", dumped) if isinstance(dumped, dict) else None
    elif isinstance(tc, dict):
        data = cast("dict[str, Any]", tc)
    else:
        data = None

    if data is not None:
        # Already in kernel shape — accept as-is.
        if "name" in data and "args" in data:
            return data
        # SDK shape with nested function descriptor.
        fn_any: Any = data.get("function")
        fn: dict[str, Any] = cast("dict[str, Any]", fn_any) if isinstance(fn_any, dict) else {}
        raw_args_any: Any = fn.get("arguments")
        args: dict[str, Any]
        if isinstance(raw_args_any, dict):
            args = cast("dict[str, Any]", raw_args_any)
        elif isinstance(raw_args_any, str):
            try:
                parsed: Any = json.loads(raw_args_any)
            except ValueError:
                parsed = None
            args = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}
        else:
            args = {}
        return {
            "id": str(data.get("id", "")),
            "name": str(fn.get("name", "")),
            "args": args,
        }

    # Non-dict, non-pydantic fallback — best-effort attribute read.
    fn_obj: Any = getattr(tc, "function", None)  # pyright: ignore[reportUnknownArgumentType]
    tc_id: Any = getattr(tc, "id", "")  # pyright: ignore[reportUnknownArgumentType]
    fn_name: Any = getattr(fn_obj, "name", "") if fn_obj is not None else ""
    return {
        "id": str(tc_id),
        "name": str(fn_name),
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
