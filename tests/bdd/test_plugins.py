"""Pytest-bdd execution of bundled plugin specs.

The Gherkin files in ``features/plugin-*.feature`` mirror
``specs/plugin-*.spec``. These step definitions exercise the real plugin
entry points through the kernel event bus so the plugin specs cannot drift
into unexecuted prose.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pytest_bdd import given, scenarios, then, when

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.tool import ToolError, ToolOk
from yaya.plugins.llm_openai.plugin import OpenAIProvider
from yaya.plugins.memory_sqlite.plugin import SqliteMemory
from yaya.plugins.mercari_jp.plugin import MercariJpSearchTool
from yaya.plugins.strategy_react import plugin as react_plugin
from yaya.plugins.tool_bash.plugin import BashTool

from .conftest import BDDContext

pytestmark = pytest.mark.unit

FEATURE_DIR = Path(__file__).parent / "features"
for _feature in (
    "plugin-llm_openai.feature",
    "plugin-mercari_jp.feature",
    "plugin-memory_sqlite.feature",
    "plugin-strategy_react.feature",
    "plugin-tool_bash.feature",
):
    scenarios(str(FEATURE_DIR / _feature))


def _kernel_ctx(bus: EventBus, tmp_path: Path, plugin_name: str) -> KernelContext:
    """Create a KernelContext for a BDD plugin scenario."""
    return KernelContext(
        bus=bus,
        logger=logging.getLogger(f"bdd.{plugin_name}"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin_name,
    )


def _fake_completion(text: str = "hi there", *, chunks: list[str] | None = None) -> Any:
    """Return an async-iterator stub matching the streaming SDK shape (#168).

    The plugin calls ``chat.completions.create`` with ``stream=True``
    and iterates the result. Each yielded chunk carries a
    ``choices[0].delta.content`` piece; the trailing chunk emits
    ``usage`` per ``stream_options={"include_usage": True}``.
    """
    body = chunks if chunks is not None else [text]

    async def _iter() -> Any:
        for piece in body:
            delta = SimpleNamespace(content=piece, tool_calls=None)
            choice = SimpleNamespace(delta=delta)
            yield SimpleNamespace(choices=[choice], usage=None)
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )

    return _iter()


def _streaming_create_mock(*, chunks: list[str] | None = None, text: str = "hi there") -> AsyncMock:
    """Build an ``AsyncMock`` whose every call returns a fresh streaming iterator."""

    async def _call(**_: Any) -> Any:
        return _fake_completion(text=text, chunks=chunks)

    return AsyncMock(side_effect=_call)


def _last_payload(ctx: BDDContext) -> dict[str, Any]:
    """Return the payload from the most recent captured event."""
    events = ctx.extras.get("captured", [])
    assert events, "no event was captured"
    event = events[-1]
    assert isinstance(event, Event)
    return event.payload


_MERCAPI_RESPONSE_WITH_ITEMS = {
    "meta": {"nextPageToken": "", "previousPageToken": "", "numFound": "1"},
    "items": [
        {
            "id": "m33333333333",
            "name": "Nintendo Switch 有機EL ホワイト 本体",
            "price": "28500",
            "status": "ITEM_STATUS_ON_SALE",
            "thumbnails": ["https://example.test/switch.jpg"],
            "itemConditionId": "3",
        }
    ],
}


def _mercapi_client(status_code: int, body: dict[str, Any] | str) -> httpx.AsyncClient:
    """Return a mocked Mercapi-compatible HTTP client for BDD scenarios."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.mercari.jp/v2/entities:search"
        payload = json.loads(request.content.decode())
        assert payload["searchCondition"]["keyword"] == "Nintendo Switch OLED"
        if isinstance(body, str):
            return httpx.Response(status_code, text=body, request=request)
        return httpx.Response(status_code, json=body, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


# -- llm-openai -------------------------------------------------------------


@given("a configured llm-openai plugin with a stubbed AsyncOpenAI client")
def _configured_openai(
    ctx: BDDContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    plugin = OpenAIProvider()
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, plugin.name)
    loop.run_until_complete(plugin.on_load(kernel_ctx))

    stub_client = MagicMock()
    stub_client.chat.completions.create = _streaming_create_mock()
    stub_client.close = AsyncMock(return_value=None)
    # D4b: instance-dispatch keyed by provider id. Register a stub
    # client under the id the scenario will publish against.
    plugin._clients["openai"] = stub_client

    async def handler(ev: Event) -> None:
        await plugin.on_event(ev, kernel_ctx)

    captured: list[Event] = []
    errors: list[Event] = []
    bus.subscribe("llm.call.request", handler, source=plugin.name)
    bus.subscribe("llm.call.response", lambda ev: _append_async(captured, ev), source="bdd")
    bus.subscribe("llm.call.error", lambda ev: _append_async(errors, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({
        "plugin": plugin,
        "kernel_ctx": kernel_ctx,
        "stub_client": stub_client,
        "captured": captured,
        "errors": errors,
        "openai_provider": "openai",
    })


async def _append_async(bucket: list[Event], ev: Event) -> None:
    bucket.append(ev)


@given("an llm-openai plugin loaded with no OPENAI_API_KEY environment variable")
def _unconfigured_openai(
    ctx: BDDContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plugin = OpenAIProvider()
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, plugin.name)
    loop.run_until_complete(plugin.on_load(kernel_ctx))

    async def handler(ev: Event) -> None:
        await plugin.on_event(ev, kernel_ctx)

    captured: list[Event] = []
    errors: list[Event] = []
    bus.subscribe("llm.call.request", handler, source=plugin.name)
    bus.subscribe("llm.call.response", lambda ev: _append_async(captured, ev), source="bdd")
    bus.subscribe("llm.call.error", lambda ev: _append_async(errors, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({
        "plugin": plugin,
        "kernel_ctx": kernel_ctx,
        "captured": captured,
        "errors": errors,
    })


@given("a configured llm-openai plugin whose stubbed client raises a RateLimitError")
def _rate_limited_openai(
    ctx: BDDContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> None:
    import httpx
    import openai

    _configured_openai(ctx, tmp_path, monkeypatch, loop)
    rate_limit_exc = openai.RateLimitError(
        message="rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    ctx.extras["stub_client"].chat.completions.create = AsyncMock(side_effect=rate_limit_exc)


@when("a llm.call.request for provider openai is published")
def _publish_openai_request(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    _publish_llm_request(ctx, loop, provider="openai")


@when("a llm.call.request for a non-openai provider id is published")
def _publish_other_llm_request(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    _publish_llm_request(ctx, loop, provider="anthropic")


def _publish_llm_request(ctx: BDDContext, loop: asyncio.AbstractEventLoop, *, provider: str) -> None:
    assert ctx.bus is not None
    req = new_event(
        "llm.call.request",
        {
            "provider": provider,
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "params": {},
        },
        session_id="bdd-openai",
        source="kernel",
    )
    ctx.extras["last_request"] = req
    loop.run_until_complete(ctx.bus.publish(req))


@then("the stubbed chat completions create method is called with the request model and messages")
def _openai_create_called(ctx: BDDContext) -> None:
    stub_client = ctx.extras["stub_client"]
    stub_client.chat.completions.create.assert_awaited_once()
    kwargs = stub_client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


@then("a llm.call.response event is emitted carrying text tool_calls usage and the originating request id")
def _openai_response_fields(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["text"] == "hi there"
    assert payload["tool_calls"] == []
    assert payload["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert payload["request_id"] == ctx.extras["last_request"].id


@then("a llm.call.error event is emitted with error not_configured")
def _openai_not_configured(ctx: BDDContext) -> None:
    assert _last_payload(ctx)["error"] == "not_configured"


@then("the response echoes the originating request id")
def _response_echoes_request_id(ctx: BDDContext) -> None:
    assert _last_payload(ctx)["request_id"] == ctx.extras["last_request"].id


@then("no llm.call.response event is emitted by the llm-openai plugin")
def _no_openai_response(ctx: BDDContext) -> None:
    assert ctx.extras["captured"] == []


@then("no llm.call.error event is emitted by the llm-openai plugin")
def _no_openai_error(ctx: BDDContext) -> None:
    assert ctx.extras["errors"] == []


@then("a llm.call.error event is emitted with the error string and the originating request id")
def _openai_error_string(ctx: BDDContext) -> None:
    errors = ctx.extras.get("errors", [])
    assert errors, "no error event was captured"
    payload = errors[-1].payload
    assert "rate limited" in payload["error"]
    assert payload["request_id"] == ctx.extras["last_request"].id


# -- streaming (#168) --------------------------------------------------------


_STREAM_CHUNKS: tuple[str, ...] = ("Hel", "lo, ", "world!")


@given("a configured llm-openai plugin whose stubbed client streams N content chunks")
def _streaming_openai(
    ctx: BDDContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    plugin = OpenAIProvider()
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, plugin.name)
    loop.run_until_complete(plugin.on_load(kernel_ctx))

    stub_client = MagicMock()
    stub_client.chat.completions.create = _streaming_create_mock(chunks=list(_STREAM_CHUNKS))
    stub_client.close = AsyncMock(return_value=None)
    plugin._clients["openai"] = stub_client

    async def handler(ev: Event) -> None:
        await plugin.on_event(ev, kernel_ctx)

    captured: list[Event] = []
    errors: list[Event] = []
    deltas: list[Event] = []
    bus.subscribe("llm.call.request", handler, source=plugin.name)
    bus.subscribe("llm.call.response", lambda ev: _append_async(captured, ev), source="bdd")
    bus.subscribe("llm.call.delta", lambda ev: _append_async(deltas, ev), source="bdd")
    bus.subscribe("llm.call.error", lambda ev: _append_async(errors, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({
        "plugin": plugin,
        "kernel_ctx": kernel_ctx,
        "stub_client": stub_client,
        "captured": captured,
        "deltas": deltas,
        "errors": errors,
        "openai_provider": "openai",
        "expected_chunks": list(_STREAM_CHUNKS),
    })


@then("N llm.call.delta events are emitted in order carrying the originating request id")
def _streaming_deltas_in_order(ctx: BDDContext) -> None:
    deltas = ctx.extras.get("deltas", [])
    chunks = ctx.extras.get("expected_chunks", [])
    assert [d.payload["content"] for d in deltas] == chunks
    req_id = ctx.extras["last_request"].id
    for d in deltas:
        assert d.payload["request_id"] == req_id


@then("a single llm.call.response event is emitted with the aggregated text and the originating request id")
def _streaming_final_response(ctx: BDDContext) -> None:
    responses = ctx.extras.get("captured", [])
    assert len(responses) == 1
    payload = responses[0].payload
    chunks = ctx.extras.get("expected_chunks", [])
    assert payload["text"] == "".join(chunks)
    assert payload["request_id"] == ctx.extras["last_request"].id


# -- mercari-jp -------------------------------------------------------------


@given("Mercapi returns a Mercari search response with visible product candidates")
def _mercari_visible_cards(ctx: BDDContext) -> None:
    ctx.extras["mercari_client"] = _mercapi_client(200, _MERCAPI_RESPONSE_WITH_ITEMS)
    ctx.extras["mercari_tool"] = MercariJpSearchTool(
        keyword="Nintendo Switch OLED",
        max_price_jpy=30_000,
        must_have=["Switch"],
    )


@given("Mercapi returns HTTP 403 for a Mercari search request")
def _mercari_forbidden(ctx: BDDContext) -> None:
    ctx.extras["mercari_client"] = _mercapi_client(403, "Forbidden")
    ctx.extras["mercari_tool"] = MercariJpSearchTool(keyword="Nintendo Switch OLED")


@given("Mercapi returns a Mercari search response with no product candidates")
def _mercari_empty(ctx: BDDContext) -> None:
    ctx.extras["mercari_client"] = _mercapi_client(
        200,
        {"meta": {"nextPageToken": "", "previousPageToken": "", "numFound": "0"}, "items": []},
    )
    ctx.extras["mercari_tool"] = MercariJpSearchTool(keyword="Nintendo Switch OLED")


@when("the mercari_jp_search tool runs with a keyword and price ceiling")
@when("the mercari_jp_search tool runs")
def _mercari_tool_runs(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    tool = ctx.extras["mercari_tool"]
    client = ctx.extras["mercari_client"]
    assert isinstance(tool, MercariJpSearchTool)
    assert isinstance(client, httpx.AsyncClient)
    result = loop.run_until_complete(tool.run_with_client(client))
    loop.run_until_complete(client.aclose())
    ctx.extras["mercari_result"] = result


@then("it returns normalized candidates with source metadata, prices, URLs, and score reasons")
def _mercari_candidates(ctx: BDDContext) -> None:
    result = ctx.extras["mercari_result"]
    assert isinstance(result, ToolOk)
    assert result.display.kind == "json"
    data = result.display.data
    item = data["items"][0]
    assert data["source"] == "mercapi_mercari"
    assert data["source_url"].startswith("https://jp.mercari.com/search?")
    assert item["price_jpy"] == 28_500
    assert item["mercari_url"] == "https://jp.mercari.com/item/m33333333333"
    assert item["score_reasons"]


@then("it returns a rejected tool error explaining that the source refused the request")
def _mercari_rejected(ctx: BDDContext) -> None:
    result = ctx.extras["mercari_result"]
    assert isinstance(result, ToolError)
    assert result.kind == "rejected"
    assert "refused" in result.display.text


@then("it returns an empty candidate list with warnings containing Mercari coverage and Japanese keyword guidance")
def _mercari_empty_result(ctx: BDDContext) -> None:
    result = ctx.extras["mercari_result"]
    assert isinstance(result, ToolOk)
    assert result.display.kind == "json"
    data = result.display.data
    assert data["items"] == []
    assert any("Mercari" in warning for warning in data["warnings"])
    assert any("keyword" in warning for warning in data["warnings"])


@given("a search request with category, brand, item_condition, and shipping_payer filters set")
def _mercari_filter_request(ctx: BDDContext) -> None:
    from yaya.plugins.mercari_jp.search import MercariSearchRequest

    ctx.extras["mercari_filter_request"] = MercariSearchRequest(
        keyword="iPhone 15",
        category_ids=[7, 1346],
        brand_ids=[9999],
        item_condition="new",
        shipping_payer="seller",
    )


@when("the Mercapi payload is built")
def _mercari_build_payload(ctx: BDDContext) -> None:
    from yaya.plugins.mercari_jp.search import build_mercapi_search_payload

    request = ctx.extras["mercari_filter_request"]
    ctx.extras["mercari_filter_payload"] = build_mercapi_search_payload(request)


@then("the payload carries the expected category, brand, condition, and shipping-payer IDs")
def _mercari_payload_carries_filters(ctx: BDDContext) -> None:
    payload = ctx.extras["mercari_filter_payload"]
    cond = payload["searchCondition"]
    assert cond["categoryId"] == ["7", "1346"]
    assert cond["brandId"] == ["9999"]
    assert cond["itemConditionId"] == ["1"]
    assert cond["shippingPayerId"] == ["2"]


# -- memory-sqlite ----------------------------------------------------------


def _load_memory(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    plugin = SqliteMemory()
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, plugin.name)
    loop.run_until_complete(plugin.on_load(kernel_ctx))

    async def handler(ev: Event) -> None:
        await plugin.on_event(ev, kernel_ctx)

    captured: list[Event] = []
    bus.subscribe("memory.query", handler, source=plugin.name)
    bus.subscribe("memory.write", handler, source=plugin.name)
    bus.subscribe("memory.result", lambda ev: _append_async(captured, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({"plugin": plugin, "kernel_ctx": kernel_ctx, "captured": captured})


@given("a loaded memory-sqlite plugin with an empty database")
@given("a loaded memory-sqlite plugin")
def _memory_loaded(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    _load_memory(ctx, tmp_path, loop)


@given("a loaded memory-sqlite plugin with one entry already persisted")
def _memory_with_duplicate_seed(
    ctx: BDDContext,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    loop: asyncio.AbstractEventLoop,
) -> None:
    _load_memory(ctx, tmp_path, loop)
    caplog.set_level(logging.WARNING, logger="bdd.memory-sqlite")
    assert ctx.bus is not None
    first = new_event(
        "memory.write",
        {"entry": {"id": "dup", "text": "first"}},
        session_id="bdd-memory",
        source="kernel",
    )
    loop.run_until_complete(ctx.bus.publish(first))


@given("a loaded memory-sqlite plugin with three entries persisted in order")
def _memory_with_three_entries(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    _load_memory(ctx, tmp_path, loop)
    assert ctx.bus is not None
    for i, ts in enumerate([1.0, 2.0, 3.0]):
        ev = new_event(
            "memory.write",
            {"entry": {"id": f"e-{i}", "text": f"row {i}", "ts": ts}},
            session_id="bdd-memory",
            source="kernel",
        )
        loop.run_until_complete(ctx.bus.publish(ev))


@when("a memory.write event is published followed by a matching memory.query")
def _memory_write_then_query(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    write = new_event(
        "memory.write",
        {"entry": {"id": "e-1", "text": "hello world"}},
        session_id="bdd-memory",
        source="kernel",
    )
    query = new_event(
        "memory.query",
        {"query": "hello", "k": 5},
        session_id="bdd-memory",
        source="kernel",
    )
    ctx.extras["last_request"] = query
    loop.run_until_complete(ctx.bus.publish(write))
    loop.run_until_complete(ctx.bus.publish(query))


@when("a memory.write event is published whose entry has no id field")
def _memory_write_without_id(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    write = new_event(
        "memory.write",
        {"entry": {"text": "anon"}},
        session_id="bdd-memory",
        source="kernel",
    )
    query = new_event(
        "memory.query",
        {"query": "anon", "k": 5},
        session_id="bdd-memory",
        source="kernel",
    )
    ctx.extras["last_request"] = query
    loop.run_until_complete(ctx.bus.publish(write))
    loop.run_until_complete(ctx.bus.publish(query))


@when("a second memory.write event is published reusing the same id")
def _memory_duplicate_id(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    ev = new_event(
        "memory.write",
        {"entry": {"id": "dup", "text": "second"}},
        session_id="bdd-memory",
        source="kernel",
    )
    loop.run_until_complete(ctx.bus.publish(ev))


@when("a memory.query event is published with an empty query string and k equal to 2")
def _memory_empty_query(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    query = new_event(
        "memory.query",
        {"query": "", "k": 2},
        session_id="bdd-memory",
        source="kernel",
    )
    ctx.extras["last_request"] = query
    loop.run_until_complete(ctx.bus.publish(query))


@then("a memory.result event is emitted with one hit whose id and text match the written entry")
def _memory_roundtrip_hit(ctx: BDDContext) -> None:
    hits = _last_payload(ctx)["hits"]
    assert len(hits) == 1
    assert hits[0]["id"] == "e-1"
    assert hits[0]["text"] == "hello world"


@then("a uuid4 hex id is persisted and appears in the next memory.result hit list")
def _memory_generated_uuid(ctx: BDDContext) -> None:
    hits = _last_payload(ctx)["hits"]
    assert len(hits) == 1
    assert len(hits[0]["id"]) == 32
    assert all(c in "0123456789abcdef" for c in hits[0]["id"])


@then("a WARNING log entry is recorded naming the duplicate id")
def _memory_duplicate_warning(caplog: pytest.LogCaptureFixture) -> None:
    assert any("duplicate id" in rec.getMessage() and "dup" in rec.getMessage() for rec in caplog.records)


@then("no exception escapes the handler")
def _no_exception_escapes(ctx: BDDContext) -> None:
    assert ctx.publish_error is None


@then("a memory.result event is emitted whose hits are the two most recent entries ordered by ts desc")
def _memory_tail_order(ctx: BDDContext) -> None:
    hits = _last_payload(ctx)["hits"]
    assert [hit["id"] for hit in hits] == ["e-2", "e-1"]


# -- strategy-react ---------------------------------------------------------


def _strategy_payload(ctx: BDDContext, payload: dict[str, Any]) -> None:
    ctx.extras["strategy_payload"] = payload


@given("a strategy.decide.request whose state has no prior assistant message")
def _strategy_no_assistant(ctx: BDDContext) -> None:
    _strategy_payload(ctx, {"state": {"messages": [{"role": "user", "content": "hi"}]}})
    ctx.extras["strategy_openai_api_key"] = "sk-test"


@given("a strategy.decide.request whose last assistant message contains a ReAct Action and Action Input")
def _strategy_tool_call(ctx: BDDContext) -> None:
    assistant_text = 'Thought: I need to echo x.\nAction: bash\nAction Input: {"cmd": ["echo", "x"]}\n'
    _strategy_payload(
        ctx,
        {
            "state": {
                "step": 0,
                "messages": [
                    {"role": "user", "content": "run something"},
                    {"role": "assistant", "content": assistant_text},
                ],
            }
        },
    )
    ctx.extras["expected_tool_call"] = {
        "id": "rx-0",
        "name": "bash",
        "args": {"cmd": ["echo", "x"]},
    }


@given("a strategy.decide.request whose last assistant message contains Final Answer prose and a [TOOL_CALL] block")
def _strategy_bracketed_tool_call(ctx: BDDContext) -> None:
    assistant_text = (
        "Final Answer: I will search Mercari Japan for XPS.\n"
        "[TOOL_CALL]\n"
        '{"tool": "mercari_jp_search", "tool_input": {"keyword": "XPS"}}\n'
        "[/TOOL_CALL]"
    )
    _strategy_payload(
        ctx,
        {
            "state": {
                "step": 0,
                "messages": [
                    {"role": "user", "content": "帮我看看mercari上的xps"},
                    {"role": "assistant", "content": assistant_text},
                ],
            }
        },
    )
    ctx.extras["expected_tool_call"] = {
        "id": "rx-0",
        "name": "mercari_jp_search",
        "args": {"keyword": "XPS"},
    }


@given("a strategy.decide.request whose state has an Observation user message after the last assistant message")
def _strategy_after_tool(ctx: BDDContext) -> None:
    _strategy_payload(
        ctx,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": 'Thought: try it.\nAction: bash\nAction Input: {"cmd": ["echo", "x"]}\n',
                    },
                    {"role": "user", "content": 'Observation: {"stdout": "x"}'},
                ],
            }
        },
    )
    ctx.extras["strategy_openai_api_key"] = "sk-test"


@given("a strategy.decide.request whose last assistant message contains a Final Answer label")
def _strategy_done(ctx: BDDContext) -> None:
    _strategy_payload(
        ctx,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Thought: greet.\nFinal Answer: hello back"},
                ]
            }
        },
    )


@given("a strategy.decide.request whose payload omits the state key entirely")
def _strategy_missing_state(ctx: BDDContext) -> None:
    _strategy_payload(ctx, {})


@when("the ReAct plugin handles the event")
def _strategy_handles(
    ctx: BDDContext,
    tmp_path: Path,
    loop: asyncio.AbstractEventLoop,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = ctx.extras.get("strategy_openai_api_key")
    if isinstance(api_key, str):
        monkeypatch.setenv("OPENAI_API_KEY", api_key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, react_plugin.name)
    captured: list[Event] = []
    bus.subscribe("strategy.decide.response", lambda ev: _append_async(captured, ev), source="bdd")
    loop.run_until_complete(react_plugin.on_load(kernel_ctx))
    req = new_event(
        "strategy.decide.request",
        ctx.extras["strategy_payload"],
        session_id="bdd-strategy",
        source="kernel",
    )
    ctx.extras["last_request"] = req
    try:
        loop.run_until_complete(react_plugin.on_event(req, kernel_ctx))
    except Exception as exc:
        ctx.extras["raised"] = exc
    ctx.extras["captured"] = captured


@then("a strategy.decide.response is emitted with next llm and the configured provider and model")
def _strategy_next_llm(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["next"] == "llm"
    # D4b: fallback provider ids mirror the D4a-seeded instance names.
    assert payload["provider"] == "llm-openai"
    assert payload["model"] == "gpt-4o-mini"


@then("a strategy.decide.response is emitted with next tool and the parsed tool_call payload")
def _strategy_next_tool(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["next"] == "tool"
    assert payload["tool_call"] == ctx.extras["expected_tool_call"]


@then("the Final Answer prose does not terminate the turn before the tool runs")
def _strategy_final_answer_does_not_preempt_tool(ctx: BDDContext) -> None:
    assert _last_payload(ctx)["next"] == "tool"


@then("a strategy.decide.response is emitted with next done")
def _strategy_next_done(ctx: BDDContext) -> None:
    assert _last_payload(ctx)["next"] == "done"


@then("the handler raises ValueError so the kernel synthesizes a plugin.error")
def _strategy_raises(ctx: BDDContext) -> None:
    assert isinstance(ctx.extras.get("raised"), ValueError)


# -- tool-bash --------------------------------------------------------------


def _load_bash(
    ctx: BDDContext,
    tmp_path: Path,
    loop: asyncio.AbstractEventLoop,
    *,
    timeout_s: float = 30.0,
) -> None:
    plugin = BashTool(timeout_s=timeout_s)
    bus = EventBus()
    kernel_ctx = _kernel_ctx(bus, tmp_path, plugin.name)
    loop.run_until_complete(plugin.on_load(kernel_ctx))

    async def handler(ev: Event) -> None:
        await plugin.on_event(ev, kernel_ctx)

    captured: list[Event] = []
    bus.subscribe("tool.call.request", handler, source=plugin.name)
    bus.subscribe("tool.call.result", lambda ev: _append_async(captured, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({"plugin": plugin, "kernel_ctx": kernel_ctx, "captured": captured})


@given("a loaded tool-bash plugin")
def _bash_loaded(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    _load_bash(ctx, tmp_path, loop)


@given("a loaded tool-bash plugin whose timeout is reduced to a small value")
def _bash_loaded_small_timeout(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    _load_bash(ctx, tmp_path, loop, timeout_s=0.2)


@when("a tool.call.request is published with name bash and args cmd equal to the echo argv list")
def _bash_echo(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    _publish_tool_request(ctx, loop, {"id": "call-1", "name": "bash", "args": {"cmd": ["echo", "hello"]}})


@when("a tool.call.request is published with name bash and args cmd equal to a single string not a list")
def _bash_bad_cmd(
    ctx: BDDContext,
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> None:
    ctx.extras["subprocess_called"] = False

    async def fail_spawn(*_: Any, **__: Any) -> Any:
        ctx.extras["subprocess_called"] = True
        raise AssertionError("subprocess should not be spawned")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)
    _publish_tool_request(ctx, loop, {"id": "call-bad", "name": "bash", "args": {"cmd": "echo hello"}})


@when("a tool.call.request runs a sleep command longer than the timeout")
def _bash_timeout(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    _publish_tool_request(ctx, loop, {"id": "call-slow", "name": "bash", "args": {"cmd": ["sleep", "5"]}})


@when("a tool.call.request for a different tool name is published")
def _bash_other_name(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    _publish_tool_request(ctx, loop, {"id": "call-other", "name": "fs", "args": {"cmd": ["echo", "hi"]}})


def _publish_tool_request(ctx: BDDContext, loop: asyncio.AbstractEventLoop, payload: dict[str, Any]) -> None:
    assert ctx.bus is not None
    req = new_event("tool.call.request", payload, session_id="bdd-bash", source="kernel")
    ctx.extras["last_request"] = req
    loop.run_until_complete(ctx.bus.publish(req))


@then("a tool.call.result event is emitted with ok true and value carrying stdout stderr and returncode zero")
def _bash_ok_result(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["ok"] is True
    assert payload["value"]["returncode"] == 0
    assert "hello" in payload["value"]["stdout"]
    assert "stderr" in payload["value"]


@then("a tool.call.result event is emitted with ok false and error mentioning argv list")
def _bash_validation_error(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["ok"] is False
    assert "argv list" in payload["error"]


@then("no subprocess is spawned")
def _bash_no_spawn(ctx: BDDContext) -> None:
    assert ctx.extras["subprocess_called"] is False


@then("the subprocess is killed by the plugin")
def _bash_process_killed(ctx: BDDContext) -> None:
    assert _last_payload(ctx)["ok"] is False


@then("a tool.call.result event is emitted with ok false and error timeout")
def _bash_timeout_result(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["ok"] is False
    assert payload["error"] == "timeout"


@then("no tool.call.result event is emitted by the tool-bash plugin")
def _bash_ignored(ctx: BDDContext) -> None:
    assert ctx.extras["captured"] == []
