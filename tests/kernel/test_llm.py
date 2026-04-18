"""Tests for the v1 llm-provider contract (``yaya.kernel.llm``).

Covers:

* :class:`~yaya.kernel.llm.TokenUsage` math, including Anthropic cache
  accounting.
* SDK-error converters for ``openai``, ``anthropic``, and raw
  ``httpx``.
* Protocol runtime-checkability (a concrete stub passes
  ``isinstance``).
* End-to-end fake provider exercised through the :class:`EventBus` —
  publishing ``llm.call.request``, emitting deltas, and observing the
  final ``llm.call.response`` with a serialised
  :class:`~yaya.kernel.llm.TokenUsage`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.llm import (
    APIConnectionError,
    APIEmptyResponseError,
    APIStatusError,
    APITimeoutError,
    ChatProviderError,
    ContentPart,
    LLMProvider,
    RetryableChatProvider,
    StreamedMessage,
    TokenUsage,
    anthropic_to_chat_provider_error,
    convert_httpx_error,
    openai_to_chat_provider_error,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- TokenUsage


def test_token_usage_defaults_zero() -> None:
    u = TokenUsage()
    assert u.input == 0
    assert u.output == 0
    assert u.total == 0


def test_token_usage_cache_math() -> None:
    """input = input_other + cache_read + cache_creation; total += output."""
    u = TokenUsage(input_other=3, input_cache_read=2, input_cache_creation=1, output=4)
    assert u.input == 6
    assert u.total == 10


def test_token_usage_serialises_with_computed_fields() -> None:
    u = TokenUsage(input_other=3, input_cache_read=2, input_cache_creation=1, output=4)
    dumped = u.model_dump()
    assert dumped["input"] == 6
    assert dumped["total"] == 10
    assert dumped["input_other"] == 3
    assert dumped["input_cache_read"] == 2
    assert dumped["input_cache_creation"] == 1
    assert dumped["output"] == 4


def test_token_usage_rejects_negative() -> None:
    with pytest.raises(ValueError):
        TokenUsage(input_other=-1)


# ---------------------------------------------------------------- openai converter


def _openai_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def test_openai_converter_connection() -> None:
    exc = openai.APIConnectionError(request=_openai_request(), message="boom")
    out = openai_to_chat_provider_error(exc)
    assert isinstance(out, APIConnectionError)
    assert "boom" in str(out)


def test_openai_converter_timeout() -> None:
    exc = openai.APITimeoutError(request=_openai_request())
    out = openai_to_chat_provider_error(exc)
    assert isinstance(out, APITimeoutError)


def test_openai_converter_status() -> None:
    response = httpx.Response(
        status_code=429,
        request=_openai_request(),
        content=b'{"error": {"message": "rate limit"}}',
    )
    exc = openai.APIStatusError(message="rate limit", response=response, body=None)
    out = openai_to_chat_provider_error(exc)
    assert isinstance(out, APIStatusError)
    assert out.status_code == 429


def test_openai_converter_unknown_is_generic() -> None:
    out = openai_to_chat_provider_error(RuntimeError("unexpected"))
    assert isinstance(out, ChatProviderError)
    # Not one of the typed subclasses.
    assert not isinstance(out, (APIConnectionError, APITimeoutError, APIStatusError))
    assert "unexpected" in str(out)


# ---------------------------------------------------------------- anthropic converter


def test_anthropic_converter_without_sdk_degrades() -> None:
    """If the anthropic SDK isn't installed, the converter returns a generic error.

    The test environment does not install ``anthropic`` — asserting the
    degraded-but-safe behaviour directly exercises the lazy-import
    branch of the converter.
    """
    out = anthropic_to_chat_provider_error(RuntimeError("boom"))
    assert isinstance(out, ChatProviderError)
    assert "boom" in str(out)


def test_anthropic_converter_maps_typed_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the anthropic SDK with a stub module exposing the typed errors.

    The converter inspects the SDK's class hierarchy via ``isinstance``;
    we install a stub module under ``anthropic`` whose classes *are* the
    exception types we raise, so each mapping branch exercises.
    """
    import sys
    import types

    stub = types.ModuleType("anthropic")

    class _APIConnectionError(Exception):
        pass

    class _APITimeoutError(_APIConnectionError):
        pass

    class _APIStatusError(Exception):
        def __init__(self, msg: str, *, status_code: int = 500, request_id: str | None = None):
            super().__init__(msg)
            self.status_code = status_code
            self.request_id = request_id

    stub.APIConnectionError = _APIConnectionError  # type: ignore[attr-defined]
    stub.APITimeoutError = _APITimeoutError  # type: ignore[attr-defined]
    stub.APIStatusError = _APIStatusError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    timeout = anthropic_to_chat_provider_error(_APITimeoutError("t"))
    assert isinstance(timeout, APITimeoutError)

    conn = anthropic_to_chat_provider_error(_APIConnectionError("c"))
    assert isinstance(conn, APIConnectionError)

    status = anthropic_to_chat_provider_error(_APIStatusError("s", status_code=503, request_id="req-1"))
    assert isinstance(status, APIStatusError)
    assert status.status_code == 503
    assert status.request_id == "req-1"

    other = anthropic_to_chat_provider_error(RuntimeError("x"))
    assert isinstance(other, ChatProviderError)


# ---------------------------------------------------------------- httpx converter


def test_convert_httpx_connect_error() -> None:
    out = convert_httpx_error(httpx.ConnectError("nope"))
    assert isinstance(out, APIConnectionError)


def test_convert_httpx_timeout() -> None:
    out = convert_httpx_error(httpx.ReadTimeout("slow"))
    assert isinstance(out, APITimeoutError)


def test_convert_httpx_status() -> None:
    req = httpx.Request("GET", "http://x")
    resp = httpx.Response(status_code=502, request=req)
    exc = httpx.HTTPStatusError("bad gateway", request=req, response=resp)
    out = convert_httpx_error(exc)
    assert isinstance(out, APIStatusError)
    assert out.status_code == 502


def test_convert_httpx_generic_http_error() -> None:
    out = convert_httpx_error(httpx.RequestError("proto"))
    assert isinstance(out, APIConnectionError)


def test_convert_httpx_unknown_is_generic() -> None:
    out = convert_httpx_error(RuntimeError("other"))
    assert isinstance(out, ChatProviderError)


# ---------------------------------------------------------------- Protocol shape


class _FakeStream:
    """Minimal :class:`StreamedMessage` implementation used by the tests."""

    def __init__(self, parts: list[ContentPart], usage: TokenUsage) -> None:
        self.id = "msg-fake"
        self._parts = parts
        self._usage = usage

    def __aiter__(self) -> AsyncIterator[ContentPart]:
        async def gen() -> AsyncIterator[ContentPart]:
            for part in self._parts:
                yield part

        return gen()

    @property
    def usage(self) -> TokenUsage:
        return self._usage


class _FakeProvider:
    """Minimal :class:`LLMProvider` stub used by the tests."""

    name = "fake"
    model_name = "fake-1"
    thinking_effort: str = "off"

    def __init__(self, parts: list[ContentPart], usage: TokenUsage) -> None:
        self._stream = _FakeStream(parts, usage)

    async def generate(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> StreamedMessage:
        return self._stream


def test_llm_provider_protocol_is_runtime_checkable() -> None:
    provider = _FakeProvider([ContentPart(text="hi")], TokenUsage(output=1))
    assert isinstance(provider, LLMProvider)


def test_streamed_message_protocol_is_runtime_checkable() -> None:
    stream = _FakeStream([ContentPart(text="hi")], TokenUsage())
    assert isinstance(stream, StreamedMessage)


class _RetryProvider(_FakeProvider):
    async def on_retryable_error(self, exc: ChatProviderError, attempt: int) -> bool:
        return attempt < 2


def test_retryable_chat_provider_protocol_detection() -> None:
    plain = _FakeProvider([ContentPart(text="hi")], TokenUsage())
    retryable = _RetryProvider([ContentPart(text="hi")], TokenUsage())
    assert not isinstance(plain, RetryableChatProvider)
    assert isinstance(retryable, RetryableChatProvider)


# ---------------------------------------------------------------- Error ctor


def test_api_status_error_carries_status_and_request_id() -> None:
    err = APIStatusError("oops", status_code=404, request_id="req-xyz")
    assert err.status_code == 404
    assert err.request_id == "req-xyz"


def test_api_empty_response_error_is_chat_provider_error() -> None:
    assert issubclass(APIEmptyResponseError, ChatProviderError)


# ------------------------------------------ end-to-end via EventBus


@pytest.mark.asyncio
async def test_fake_provider_streams_through_bus() -> None:
    """Exercise the full contract shape through the real :class:`EventBus`.

    The scenario mirrors AC-01 from
    ``specs/llm-provider-contract.spec``: a fake provider yields two
    content parts; the subscriber re-emits each as ``llm.call.delta``
    and a single terminal ``llm.call.response`` carrying the
    serialised :class:`TokenUsage`.
    """
    bus = EventBus()

    provider = _FakeProvider(
        [ContentPart(text="hel"), ContentPart(text="lo")],
        TokenUsage(input_other=3, output=2),
    )

    deltas: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_request(ev: Event) -> None:
        stream = await provider.generate(system_prompt="", tools=[], history=[])
        text_buf: list[str] = []
        async for part in stream:
            if isinstance(part, ContentPart):
                text_buf.append(part.text)
                await bus.publish(
                    new_event(
                        "llm.call.delta",
                        {"content": part.text, "request_id": ev.id},
                        session_id=ev.session_id,
                        source="fake",
                    )
                )
        await bus.publish(
            new_event(
                "llm.call.response",
                {
                    "text": "".join(text_buf),
                    "usage": stream.usage.model_dump(),
                    "request_id": ev.id,
                },
                session_id=ev.session_id,
                source="fake",
            )
        )

    async def on_delta(ev: Event) -> None:
        deltas.append(dict(ev.payload))

    async def on_response(ev: Event) -> None:
        responses.append(dict(ev.payload))
        done.set()

    bus.subscribe("llm.call.request", on_request, source="fake")
    bus.subscribe("llm.call.delta", on_delta, source="test")
    bus.subscribe("llm.call.response", on_response, source="test")

    try:
        await bus.publish(
            new_event(
                "llm.call.request",
                {"provider": "fake", "model": "fake-1", "messages": [], "params": {}},
                session_id="s-1",
                source="test",
            )
        )
        await asyncio.wait_for(done.wait(), timeout=2.0)
    finally:
        await bus.close()

    assert [d["content"] for d in deltas] == ["hel", "lo"]
    assert len(responses) == 1
    resp = responses[0]
    assert resp["text"] == "hello"
    usage = resp["usage"]
    assert usage["input"] == 3
    assert usage["output"] == 2
    assert usage["total"] == 5
