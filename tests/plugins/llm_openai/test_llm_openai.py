"""Tests for the OpenAI LLM-provider plugin.

AC-bindings from ``specs/plugin-llm_openai.spec``:

* success → ``test_successful_completion_emits_response``
* missing key → ``test_missing_api_key_emits_not_configured_error``
* filter → ``test_non_matching_provider_is_ignored``
* rate limit → ``test_rate_limit_error_emits_error_event``

Uses ``unittest.mock`` to stub ``openai.AsyncOpenAI`` so tests stay
offline and deterministic. ``pytest-httpx`` is available but injecting
a stub client is simpler than intercepting raw HTTP.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.llm_openai.plugin import OpenAIProvider


def _make_ctx(bus: EventBus, tmp_path: Path, plugin: OpenAIProvider) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.llm-openai"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )


def _fake_completion(
    text: str = "hi there",
    *,
    input_tokens: int = 7,
    output_tokens: int = 3,
) -> SimpleNamespace:
    """Build a stub mirroring the SDK's chat.completion object shape."""
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_successful_completion_emits_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful chat completion emits llm.call.response with all fields."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)

    # Inject a stub client before on_event so we don't hit the network.
    await plugin.on_load(ctx)
    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=_fake_completion())
    stub_client.close = AsyncMock(return_value=None)
    plugin._client = stub_client

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("llm.call.request", _handler, source=plugin.name)

    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("llm.call.response", _observer, source="observer")

    req = new_event(
        "llm.call.request",
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "params": {},
        },
        session_id="sess-openai-ok",
        source="kernel",
    )
    await bus.publish(req)

    stub_client.chat.completions.create.assert_awaited_once()
    kwargs = stub_client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["text"] == "hi there"
    assert payload["tool_calls"] == []
    assert payload["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert payload["request_id"] == req.id

    await plugin.on_unload(ctx)


async def test_missing_api_key_emits_not_configured_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no API key, every request emits not_configured error."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("llm.call.request", _handler, source=plugin.name)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("llm.call.error", _observer, source="observer")

    req = new_event(
        "llm.call.request",
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [],
            "params": {},
        },
        session_id="sess-noauth",
        source="kernel",
    )
    await bus.publish(req)

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["error"] == "not_configured"
    assert payload["request_id"] == req.id

    await plugin.on_unload(ctx)


async def test_non_matching_provider_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A request for a sibling provider does not emit any event from llm-openai."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(return_value=_fake_completion())
    stub_client.close = AsyncMock(return_value=None)
    plugin._client = stub_client

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("llm.call.request", _handler, source=plugin.name)
    responses: list[Event] = []
    errors: list[Event] = []

    async def _r(ev: Event) -> None:
        responses.append(ev)

    async def _e(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("llm.call.response", _r, source="observer")
    bus.subscribe("llm.call.error", _e, source="observer")

    await bus.publish(
        new_event(
            "llm.call.request",
            {
                "provider": "anthropic",
                "model": "claude-3-5",
                "messages": [],
                "params": {},
            },
            session_id="sess-anthropic",
            source="kernel",
        )
    )

    assert responses == []
    assert errors == []
    stub_client.chat.completions.create.assert_not_called()

    await plugin.on_unload(ctx)


async def test_rate_limit_error_emits_error_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An SDK RateLimitError surfaces as llm.call.error with str(exc) + request_id.

    We build the SDK's real ``RateLimitError`` from a 429 response so
    the ``isinstance(exc, RateLimitError)`` branch in ``_emit_error``
    runs. If a future SDK minor version tightens the constructor and
    this construction breaks, fall back to ``side_effect=Exception(
    "rate limited")`` — the plugin translates any exception to
    ``llm.call.error`` regardless of subclass.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    import httpx
    import openai

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    rate_limit_exc = openai.RateLimitError(
        message="rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
        body=None,
    )

    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(side_effect=rate_limit_exc)
    stub_client.close = AsyncMock(return_value=None)
    plugin._client = stub_client

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("llm.call.request", _handler, source=plugin.name)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("llm.call.error", _observer, source="observer")

    req = new_event(
        "llm.call.request",
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [],
            "params": {},
        },
        session_id="sess-ratelimit",
        source="kernel",
    )
    await bus.publish(req)

    assert len(captured) == 1
    payload = captured[0].payload
    assert "rate limited" in payload["error"]
    assert payload["request_id"] == req.id

    await plugin.on_unload(ctx)


async def test_config_updated_rebuilds_on_matching_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``config.updated`` for ``plugin.llm_openai.base_url`` rebuilds the client."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    calls: list[dict[str, Any]] = []

    class _StubSDK:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)
    built_once = len(calls)
    assert built_once == 1

    ev = new_event(
        "config.updated",
        {"key": "plugin.llm_openai.base_url", "prefix_match_hint": "plugin.llm_openai."},
        session_id="kernel",
        source="kernel-config-store",
    )
    await plugin.on_event(ev, ctx)
    assert len(calls) == built_once + 1

    # Non-matching suffix — e.g. a sibling key like ``plugin.llm_openai.model`` —
    # does NOT rebuild the client.
    unrelated = new_event(
        "config.updated",
        {"key": "plugin.llm_openai.model", "prefix_match_hint": "plugin.llm_openai."},
        session_id="kernel",
        source="kernel-config-store",
    )
    await plugin.on_event(unrelated, ctx)
    assert len(calls) == built_once + 1

    # Entirely foreign namespace — no rebuild.
    foreign = new_event(
        "config.updated",
        {"key": "plugin.other.base_url", "prefix_match_hint": "plugin.other."},
        session_id="kernel",
        source="kernel-config-store",
    )
    await plugin.on_event(foreign, ctx)
    assert len(calls) == built_once + 1

    # Payload without a key — ignored silently.
    empty = new_event(
        "config.updated",
        {"key": 12345, "prefix_match_hint": ""},  # type: ignore[dict-item]
        session_id="kernel",
        source="kernel-config-store",
    )
    await plugin.on_event(empty, ctx)
    assert len(calls) == built_once + 1

    await plugin.on_unload(ctx)


async def test_base_url_env_lands_on_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``OPENAI_BASE_URL`` is threaded into the SDK constructor when ctx.config is empty."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example")

    calls: list[dict[str, Any]] = []

    class _StubSDK:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)
    assert calls
    assert calls[-1].get("base_url") == "https://env.example"
    await plugin.on_unload(ctx)


async def test_rebuild_swallows_stale_client_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale client's ``close()`` raising is logged, not re-raised."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    # Swap the live client for one whose close() raises so the
    # stale-close ``except Exception`` branch in ``_rebuild_client`` runs.
    bad_client = MagicMock()
    bad_client.close = AsyncMock(side_effect=RuntimeError("close failed"))
    plugin._client = bad_client

    ev = new_event(
        "config.updated",
        {"key": "plugin.llm_openai.base_url", "prefix_match_hint": "plugin.llm_openai."},
        session_id="kernel",
        source="kernel-config-store",
    )
    # Should not raise — the failure is swallowed + logged.
    await plugin.on_event(ev, ctx)
    bad_client.close.assert_awaited_once()
    await plugin.on_unload(ctx)


async def test_subscribes_to_config_updated(tmp_path: Path) -> None:
    """The plugin declares a subscription to ``config.updated`` for hot reload."""
    plugin = OpenAIProvider()
    assert "config.updated" in plugin.subscriptions()
    assert "llm.call.request" in plugin.subscriptions()


def _silence(_: Any) -> None:  # pragma: no cover - placeholder to keep Any imported.
    return None
