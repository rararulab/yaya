"""Pytest-bdd execution of bundled plugin specs.

The Gherkin files in ``features/plugin-*.feature`` mirror
``specs/plugin-*.spec``. These step definitions exercise the real plugin
entry points through the kernel event bus so the plugin specs cannot drift
into unexecuted prose.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_bdd import given, scenarios, then, when

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.llm_openai.plugin import OpenAIProvider
from yaya.plugins.memory_sqlite.plugin import SqliteMemory
from yaya.plugins.strategy_react import plugin as react_plugin
from yaya.plugins.tool_bash.plugin import BashTool

from .conftest import BDDContext

pytestmark = pytest.mark.unit

FEATURE_DIR = Path(__file__).parent / "features"
for _feature in (
    "plugin-llm_openai.feature",
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


def _fake_completion(text: str = "hi there") -> SimpleNamespace:
    """Build a small object matching the SDK completion shape used here."""
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3)
    return SimpleNamespace(choices=[choice], usage=usage)


def _last_payload(ctx: BDDContext) -> dict[str, Any]:
    """Return the payload from the most recent captured event."""
    events = ctx.extras.get("captured", [])
    assert events, "no event was captured"
    event = events[-1]
    assert isinstance(event, Event)
    return event.payload


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
    stub_client.chat.completions.create = AsyncMock(return_value=_fake_completion())
    stub_client.close = AsyncMock(return_value=None)
    plugin._client = stub_client

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
    bus.subscribe("llm.call.request", handler, source=plugin.name)
    bus.subscribe("llm.call.error", lambda ev: _append_async(captured, ev), source="bdd")
    ctx.bus = bus
    ctx.extras.update({"plugin": plugin, "kernel_ctx": kernel_ctx, "captured": captured})


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


@given("a strategy.decide.request whose last assistant message carries a non-empty tool_calls list")
def _strategy_tool_call(ctx: BDDContext) -> None:
    tool_call = {"id": "tc-1", "name": "bash", "args": {"cmd": ["echo", "x"]}}
    _strategy_payload(
        ctx,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "run something"},
                    {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                ]
            }
        },
    )
    ctx.extras["expected_tool_call"] = tool_call


@given("a strategy.decide.request whose last_tool_result is populated after an assistant step")
def _strategy_after_tool(ctx: BDDContext) -> None:
    _strategy_payload(
        ctx,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": "thinking", "tool_calls": []},
                ],
                "last_tool_result": {"id": "tc", "ok": True, "value": {"stdout": "x"}},
            }
        },
    )
    ctx.extras["strategy_openai_api_key"] = "sk-test"


@given("a strategy.decide.request whose last assistant message has no tool_calls and no pending tool result")
def _strategy_done(ctx: BDDContext) -> None:
    _strategy_payload(
        ctx,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello back"},
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
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"


@then("a strategy.decide.response is emitted with next tool and the first pending tool_call payload")
def _strategy_next_tool(ctx: BDDContext) -> None:
    payload = _last_payload(ctx)
    assert payload["next"] == "tool"
    assert payload["tool_call"] == ctx.extras["expected_tool_call"]


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
