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
    """An SDK RateLimitError surfaces as llm.call.error with str(exc) + request_id."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Build a minimal RateLimitError-ish exception that isinstance-matches.
    import openai

    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    # SDK RateLimitError requires a message + response + body; a subclass with
    # a simple ``__init__`` avoids depending on private constructor shape.
    class _FakeRateLimit(openai.RateLimitError):
        def __init__(self) -> None:
            Exception.__init__(self, "rate limited")

        def __str__(self) -> str:
            return "rate limited"

    stub_client = MagicMock()
    stub_client.chat.completions.create = AsyncMock(side_effect=_FakeRateLimit())
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


def _silence(_: Any) -> None:  # pragma: no cover - placeholder to keep Any imported.
    return None
