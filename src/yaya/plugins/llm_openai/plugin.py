"""OpenAI LLM-provider plugin implementation.

The plugin is **instance-scoped**: after #123 (D4b) it maintains one
:class:`openai.AsyncOpenAI` client per configured ``providers.<id>.*``
row whose ``plugin`` meta equals ``llm-openai``. Dispatch happens by
matching ``ev.payload["provider"]`` (an *instance id*, not the plugin
name) against the plugin's owned-client dict. One plugin process
therefore backs many operator-configured instances — e.g. "OpenAI
prod", "Azure OpenAI", "local-LM-Studio" — each with its own
``api_key``, ``base_url``, and ``model`` fields.

Chat completions stream by default (#168): the plugin passes
``stream=True`` with ``stream_options={"include_usage": True}`` and
consumes the SDK's async iterator, emitting one ``llm.call.delta``
per user-visible text chunk and a final ``llm.call.response`` with
the aggregated content plus usage (when the upstream provides it).
``<think>...</think>`` reasoning tags are filtered via a chunk-
boundary-safe state machine (:class:`_StreamThinkFilter`) so partial
tags never leak to adapters and the aggregated response matches the
non-streaming behaviour from #149. Every response event echoes
``request_id`` so the agent loop's ``_RequestTracker`` correlates
concurrent calls (lesson #15 in ``docs/wiki/lessons-learned.md``).

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
        """Stream chat completions and emit deltas + a final response.

        The SDK is invoked with ``stream=True`` and
        ``stream_options={"include_usage": True}``. Each chunk's
        ``delta.content`` is fed through a
        :class:`_StreamThinkFilter` so ``<think>...</think>`` spans
        (even when split across chunk boundaries) are suppressed
        from ``llm.call.delta`` events and from the aggregated
        ``llm.call.response`` text — matching the non-streaming
        behaviour that #149 introduced.

        The final ``llm.call.response`` carries:

        * ``text`` — aggregated post-filter content.
        * ``tool_calls`` — list of accumulated tool calls. Chunks
          deliver tool calls via ``delta.tool_calls`` indexed by
          ``index``; we reassemble by index and normalise through
          :func:`_tool_call_to_dict`.
        * ``usage`` — ``{"input_tokens": int, "output_tokens": int}``
          when the upstream supports ``stream_options.include_usage``
          (OpenAI-native). ``None`` on providers that ignore the
          option (some OpenAI-compatible endpoints).

        Model selection is a live read against
        :attr:`ctx.providers` so ``yaya config set
        providers.<id>.model gpt-4.1`` takes effect on the next turn
        without a rebuild.
        """
        client = self._clients[instance_id]
        model = self._resolve_model(ctx, instance_id, ev.payload)
        payload = ev.payload
        messages = cast("list[dict[str, Any]]", payload.get("messages") or [])
        tools = payload.get("tools")

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            # Opt into the final usage chunk. OpenAI-native honors this;
            # endpoints that don't understand ``stream_options`` should
            # ignore it (and we tolerate a missing usage below).
            "stream_options": {"include_usage": True},
        }
        if tools:
            create_kwargs["tools"] = tools

        # The SDK typing union (ChatCompletion | AsyncStream[...]) widens
        # everything past this point to Any — that matches the tests'
        # stub-client pattern and the runtime shape we actually emit.
        stream: Any = await client.chat.completions.create(**create_kwargs)  # pyright: ignore[reportUnknownVariableType]

        think_filter = _StreamThinkFilter()
        aggregated: list[str] = []
        tool_call_buf: dict[int, dict[str, Any]] = {}
        usage_obj: Any = None

        async for chunk in stream:  # pyright: ignore[reportUnknownVariableType]
            usage_obj = await self._consume_chunk(ev, ctx, chunk, think_filter, aggregated, tool_call_buf, usage_obj)

        # Drain any buffer the filter was sitting on when the stream ended.
        tail_visible, tail_retained = think_filter.flush()
        if tail_retained:
            aggregated.append(tail_retained)
        if tail_visible:
            await ctx.emit(
                "llm.call.delta",
                {"request_id": ev.id, "content": tail_visible},
                session_id=ev.session_id,
            )

        text = "".join(aggregated).strip() if think_filter.stripped_any else "".join(aggregated)
        tool_calls = [_tool_call_to_dict(tc) for tc in tool_call_buf.values()]
        usage = _extract_usage(usage_obj)

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
    async def _consume_chunk(
        ev: Event,
        ctx: KernelContext,
        chunk: Any,
        think_filter: _StreamThinkFilter,
        aggregated: list[str],
        tool_call_buf: dict[int, dict[str, Any]],
        usage_obj: Any,
    ) -> Any:
        """Absorb one streamed chunk; return the (possibly updated) ``usage`` sentinel.

        Extracted from :meth:`_dispatch` to keep its cyclomatic
        complexity under the lint gate — the per-chunk work has its
        own branching (usage trailer vs content chunk vs tool-call
        chunk) that would otherwise inflate the async-for body.
        """
        chunk_usage: Any = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage_obj = chunk_usage

        choices_raw: Any = getattr(chunk, "choices", None) or []
        choices: list[Any] = list(choices_raw) if choices_raw else []
        if not choices:
            return usage_obj
        delta: Any = getattr(choices[0], "delta", None)
        if delta is None:
            return usage_obj

        content_piece_any: Any = getattr(delta, "content", None)
        if isinstance(content_piece_any, str) and content_piece_any:
            visible, retained = think_filter.feed(content_piece_any)
            if retained:
                aggregated.append(retained)
            if visible:
                await ctx.emit(
                    "llm.call.delta",
                    {"request_id": ev.id, "content": visible},
                    session_id=ev.session_id,
                )

        delta_tool_calls_any: Any = getattr(delta, "tool_calls", None) or []
        if delta_tool_calls_any:
            for partial in list(delta_tool_calls_any):
                _merge_tool_call_chunk(tool_call_buf, partial)
        return usage_obj

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
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class _StreamThinkFilter:
    """Chunk-boundary-safe filter for inline ``<think>...</think>`` spans.

    The non-streaming path uses :func:`_strip_reasoning_tags`, a
    single regex pass. Streaming cannot: a chunk may arrive mid-tag
    (``"<thi"`` / ``"nk>reasoning"`` / ``"</think>done"``) and
    emitting any of those bytes to adapters would either flash raw
    markers at the user or leak reasoning into the UI bubble.

    State machine:

    * ``OUTSIDE``: buffering until we are sure the trailing bytes are
      not the start of ``<think>``. When we encounter ``<`` we hold
      everything from that point until either the full ``<think>``
      open tag is matched (→ ``INSIDE``) or enough following bytes
      prove it is NOT a ``<think>`` open (→ flush the held bytes
      back to ``visible``).
    * ``INSIDE``: buffering and suppressing every byte until the
      ``</think>`` close tag lands. On close, flip back to
      ``OUTSIDE`` and continue — trailing bytes from the same chunk
      are processed recursively so one chunk closing a span and
      starting the next segment still emits the tail.

    Returns on :meth:`feed` a pair ``(visible, retained)`` where
    ``visible`` is what adapters should see immediately (``delta``
    events) and ``retained`` is what the aggregated response text
    should accumulate — the two are the same under normal operation
    (we only emit bytes we plan to keep) but are returned separately
    so future consumers can diverge (e.g. debug-log thought spans
    while not emitting them).
    """

    __slots__ = ("_buf", "_in_think", "stripped_any")

    def __init__(self) -> None:
        self._buf: str = ""
        self._in_think: bool = False
        self.stripped_any: bool = False
        """Whether any ``<think>`` span was observed — mirrors the
        post-regex ``.strip()`` call in :func:`_strip_reasoning_tags`
        so the aggregated output matches byte-for-byte on the common
        MiniMax pattern."""

    def feed(self, chunk: str) -> tuple[str, str]:
        """Consume one streamed chunk and return ``(visible, retained)``."""
        self._buf += chunk
        visible_parts: list[str] = []
        while self._buf:
            if self._in_think:
                if not self._step_inside():
                    break
                continue
            if not self._step_outside(visible_parts):
                break
        visible = "".join(visible_parts)
        return visible, visible

    def _step_inside(self) -> bool:
        """Advance while inside a ``<think>`` span; return True to keep stepping."""
        close_idx = self._buf.find(_THINK_CLOSE)
        if close_idx == -1:
            # Retain only the trailing bytes that could still be a
            # partial close tag (``</``, ``</t``, ...); drop the
            # rest — it is safely inside the span.
            keep = 0
            for n in range(1, min(len(_THINK_CLOSE), len(self._buf) + 1)):
                if _THINK_CLOSE.startswith(self._buf[-n:]):
                    keep = n
            self._buf = self._buf[-keep:] if keep else ""
            return False
        self._buf = self._buf[close_idx + len(_THINK_CLOSE) :]
        self._in_think = False
        self.stripped_any = True
        return True

    def _step_outside(self, visible_parts: list[str]) -> bool:
        """Advance while outside a ``<think>`` span; return True to keep stepping."""
        lt_idx = self._buf.find("<")
        if lt_idx == -1:
            visible_parts.append(self._buf)
            self._buf = ""
            return False
        if lt_idx > 0:
            visible_parts.append(self._buf[:lt_idx])
            self._buf = self._buf[lt_idx:]
        if len(self._buf) < len(_THINK_OPEN):
            if _THINK_OPEN.startswith(self._buf):
                return False  # wait for more bytes.
            visible_parts.append(self._buf)
            self._buf = ""
            return False
        if self._buf.startswith(_THINK_OPEN):
            self._buf = self._buf[len(_THINK_OPEN) :]
            self._in_think = True
            return True
        # Not a ``<think>`` open — emit the literal ``<`` and keep
        # scanning from the next byte.
        visible_parts.append(self._buf[0])
        self._buf = self._buf[1:]
        return True

    def flush(self) -> tuple[str, str]:
        """Drain any buffered bytes at end-of-stream.

        If the stream ended mid-``<think>`` the held body is
        **dropped**. This deliberately diverges from the non-
        streaming regex path (``_strip_reasoning_tags``) which keeps
        an unclosed ``<think>`` verbatim — under streaming we cannot
        show the user a half-reasoning span that was going to be
        stripped by the closing tag, so dropping is safer for the UI.
        Rare enough in practice (MiniMax always closes its ``<think>``
        blocks) that the protocol-level divergence is acceptable.

        If the stream ended mid-``<`` (ambiguous partial prefix of
        ``<think>``), flush the literal bytes since the tag never
        completed.
        """
        if self._in_think:
            # Unclosed ``<think>`` — drop the body.
            self._buf = ""
            return "", ""
        tail = self._buf
        self._buf = ""
        return tail, tail


def _extract_usage(usage_obj: Any) -> dict[str, int] | None:
    """Translate a provider ``usage`` sentinel to the kernel usage dict or ``None``.

    OpenAI delivers ``usage`` on the trailing stream chunk only when
    the request opted in via ``stream_options={"include_usage":
    True}``. Some OpenAI-compatible endpoints silently drop the
    option and never emit a usage sentinel — we tolerate that path by
    returning ``None`` rather than forging zeros that a downstream
    token-budget gauge could mistake for "this turn was free".
    """
    if usage_obj is None:
        return None
    input_tokens = getattr(usage_obj, "prompt_tokens", None)
    output_tokens = getattr(usage_obj, "completion_tokens", None)
    usage: dict[str, int] = {}
    if input_tokens is not None:
        usage["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        usage["output_tokens"] = int(output_tokens)
    return usage


def _merge_tool_call_chunk(buf: dict[int, dict[str, Any]], partial: Any) -> None:
    """Fold one streamed ``delta.tool_calls`` entry into the buffer.

    OpenAI streams tool calls across chunks: each chunk carries
    ``delta.tool_calls[*]`` whose ``index`` field aligns entries
    across chunks. ``function.name`` and ``function.arguments`` are
    *concatenated* across chunks; ``id`` lands on the first chunk
    for that index. We keep the accumulated entry in the SDK's
    nested shape so :func:`_tool_call_to_dict` — already exercised
    by the non-streaming path — handles normalisation unchanged.
    """
    index_raw: Any = getattr(partial, "index", None)
    if not isinstance(index_raw, int):
        # Some compatible endpoints may omit ``index`` and send one
        # complete tool call per chunk. Bucket by id in that case so
        # we still aggregate sensibly.
        fallback_id_raw: Any = getattr(partial, "id", None) or ""
        fallback_id = fallback_id_raw if isinstance(fallback_id_raw, str) else ""
        index = -(len(buf) + 1) if not fallback_id else hash(fallback_id)
    else:
        index = index_raw

    entry = buf.setdefault(
        index,
        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
    )
    tc_id: Any = getattr(partial, "id", None)
    if isinstance(tc_id, str) and tc_id and not entry["id"]:
        entry["id"] = tc_id

    fn_obj: Any = getattr(partial, "function", None)
    if fn_obj is not None:
        fn_name: Any = getattr(fn_obj, "name", None)
        fn_args: Any = getattr(fn_obj, "arguments", None)
        fn_dict = cast("dict[str, Any]", entry["function"])
        if isinstance(fn_name, str) and fn_name:
            fn_dict["name"] = fn_dict["name"] + fn_name if fn_dict["name"] else fn_name
        if isinstance(fn_args, str) and fn_args:
            fn_dict["arguments"] = fn_dict["arguments"] + fn_args


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
