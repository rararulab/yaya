"""Tests for the ReAct strategy plugin.

AC-bindings from ``specs/plugin-strategy_react.spec`` and
``specs/plugin-instance-dispatch.spec``:

* no assistant yet → ``test_no_assistant_yet_returns_llm``
* pending tool_calls → ``test_assistant_with_tool_calls_returns_tool``
* after tool result → ``test_after_tool_result_returns_llm``
* assistant done → ``test_assistant_without_tool_calls_returns_done``
* missing state → ``test_missing_state_raises``
* model from instance → ``test_provider_and_model_reads_instance_config``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.strategy_react import plugin as react_plugin
from yaya.plugins.strategy_react.plugin import ReActStrategy


def _make_ctx(
    bus: EventBus,
    tmp_path: Path,
    *,
    store: ConfigStore | None = None,
) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.strategy-react"),
        config={},
        state_dir=tmp_path,
        plugin_name=react_plugin.name,
        config_store=store,
    )


async def _drive(
    bus: EventBus,
    plugin: Any,
    tmp_path: Path,
    payload: dict[str, Any],
    *,
    session_id: str,
    store: ConfigStore | None = None,
) -> list[Event]:
    """Publish one strategy.decide.request and return the captured responses."""
    ctx = _make_ctx(bus, tmp_path, store=store)
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("strategy.decide.request", _handler, source=plugin.name)

    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("strategy.decide.response", _observer, source="observer")

    req = new_event(
        "strategy.decide.request",
        payload,
        session_id=session_id,
        source="kernel",
    )
    await bus.publish(req)
    return captured


async def test_no_assistant_yet_returns_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No assistant message → llm with provider + model + request_id.

    Pins ``OPENAI_API_KEY`` so the strategy's env-sniff fallback (used
    when ``ctx.providers`` is absent) resolves to the ``llm-openai``
    branch.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {"state": {"messages": [{"role": "user", "content": "hi"}]}},
        session_id="sess-llm-1",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "llm"
    assert got["provider"] == "llm-openai"
    assert got["model"] == "gpt-4o-mini"
    assert "request_id" in got


async def test_assistant_with_tool_calls_returns_tool(tmp_path: Path) -> None:
    """Assistant with tool_calls → tool with the first call."""
    bus = EventBus()
    tool_call = {"id": "tc-1", "name": "bash", "args": {"cmd": ["echo", "x"]}}
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "run something"},
                    {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                ],
            }
        },
        session_id="sess-tool-1",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "tool"
    assert got["tool_call"] == tool_call
    assert "request_id" in got


async def test_after_tool_result_returns_llm(tmp_path: Path) -> None:
    """Assistant then tool result → back to llm."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": "thinking", "tool_calls": []},
                ],
                "last_tool_result": {"id": "tc", "ok": True, "value": {"stdout": "x"}},
            }
        },
        session_id="sess-after-tool",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "llm"
    assert "request_id" in got


async def test_assistant_without_tool_calls_returns_done(tmp_path: Path) -> None:
    """Assistant with no tool_calls and no pending tool result → done."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello back"},
                ],
            }
        },
        session_id="sess-done-1",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "done"
    assert "request_id" in got


async def test_missing_state_raises(tmp_path: Path) -> None:
    """Missing state key raises ValueError so the kernel synthesizes plugin.error."""
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path)
    await react_plugin.on_load(ctx)
    req = new_event(
        "strategy.decide.request",
        {},
        session_id="sess-missing",
        source="kernel",
    )
    with pytest.raises(ValueError, match="state"):
        await react_plugin.on_event(req, ctx)


async def test_provider_and_model_reads_instance_config(tmp_path: Path) -> None:
    """AC-06: strategy reads ``model`` from the active instance's config."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.prod.plugin", "llm-openai")
        await store.set("providers.prod.model", "gpt-4.1")
        await store.set("provider", "prod")

        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert provider == "prod"
        assert model == "gpt-4.1"
    finally:
        await store.close()


async def test_provider_and_model_switches_on_active_change(tmp_path: Path) -> None:
    """AC-02: flipping ``provider`` routes the next decision to the new instance."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.a.plugin", "llm-openai")
        await store.set("providers.a.model", "gpt-a")
        await store.set("providers.b.plugin", "llm-openai")
        await store.set("providers.b.model", "gpt-b")
        await store.set("provider", "a")

        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("a", "gpt-a")

        await store.set("provider", "b")
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("b", "gpt-b")
    finally:
        await store.close()


async def test_provider_and_model_falls_back_to_first_instance(tmp_path: Path) -> None:
    """When ``provider`` is unset but instances exist, fall back to the first."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.only.plugin", "llm-openai")
        await store.set("providers.only.model", "gpt-only")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("only", "gpt-only")
    finally:
        await store.close()


async def test_provider_and_model_echo_instance_gets_echo_model(tmp_path: Path) -> None:
    """An echo-backed instance with no explicit model falls through to ``echo``."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.local-echo.plugin", "llm-echo")
        await store.set("provider", "local-echo")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("local-echo", "echo")
    finally:
        await store.close()


async def test_provider_and_model_unknown_active_falls_back_to_first(tmp_path: Path) -> None:
    """An ``active_id`` that does not resolve to an instance falls back to the first."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.only.plugin", "llm-openai")
        await store.set("providers.only.model", "gpt-only")
        await store.set("provider", "missing-id")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("only", "gpt-only")
    finally:
        await store.close()
