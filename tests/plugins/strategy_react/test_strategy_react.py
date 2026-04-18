"""Tests for the ReAct strategy plugin.

AC-bindings from ``specs/plugin-strategy_react.spec``:

* no assistant yet → ``test_no_assistant_yet_returns_llm``
* pending tool_calls → ``test_assistant_with_tool_calls_returns_tool``
* after tool result → ``test_after_tool_result_returns_llm``
* assistant done → ``test_assistant_without_tool_calls_returns_done``
* missing state → ``test_missing_state_raises``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.strategy_react import plugin as react_plugin


def _make_ctx(bus: EventBus, tmp_path: Path) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.strategy-react"),
        config={},
        state_dir=tmp_path,
        plugin_name=react_plugin.name,
    )


async def _drive(
    bus: EventBus,
    plugin: Any,
    tmp_path: Path,
    payload: dict[str, Any],
    *,
    session_id: str,
) -> list[Event]:
    """Publish one strategy.decide.request and return the captured responses."""
    ctx = _make_ctx(bus, tmp_path)
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


async def test_no_assistant_yet_returns_llm(tmp_path: Path) -> None:
    """No assistant message → llm with provider + model + request_id."""
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
    assert got["provider"] == "openai"
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
