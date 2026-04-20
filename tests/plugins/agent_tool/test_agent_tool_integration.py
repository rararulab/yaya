"""End-to-end integration test for the agent-tool plugin.

Wires a real :class:`EventBus`, real :class:`AgentLoop`, the bundled
``strategy_react`` and ``llm_echo`` plugins, and the ``agent_tool``
plugin into one process — no fakes. Publishes a single
``tool.call.request`` for the ``agent`` tool and asserts the
``tool.call.result`` arrives with ``ok=True``. Proves the bus
re-entry claim in the PR body: ``AgentTool.run`` forks the parent
session, publishes ``user.message.received`` on the child, and the
running :class:`AgentLoop` picks it up, drives the strategy / LLM
round-trip, and emits ``assistant.message.done`` on the child, which
resolves the tool's return envelope.

The approval runtime is intentionally not installed so
:meth:`Tool.pre_approve` falls through to the allow-all default.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.loop import AgentLoop, LoopConfig
from yaya.kernel.plugin import KernelContext
from yaya.kernel.session import MemoryTapeStore, SessionStore
from yaya.kernel.tool import ToolOk, _clear_tool_registry, install_dispatcher
from yaya.plugins.agent_tool.plugin import AgentPlugin, _Runtime
from yaya.plugins.llm_echo.plugin import EchoLLM
from yaya.plugins.strategy_react.plugin import ReActStrategy

from ._fake_strategy import collect

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    """Clear the module-level tool registry + runtime cache around each test."""
    _clear_tool_registry()
    yield
    _clear_tool_registry()
    _Runtime.session = None
    _Runtime.bus = None
    _Runtime.plugin_ctx = None


async def test_agent_tool_bus_reentry_end_to_end(tmp_path: Path) -> None:
    """Real AgentLoop + echo LLM + ReAct strategy round-trip through AgentTool.

    The echo provider prefixes any user message with ``(echo) ``, so a
    goal like ``"say HELLO and stop"`` yields final text
    ``"(echo) say HELLO and stop"``. We assert a non-empty string came
    back rather than an exact prefix match — the point is the whole
    pipeline connects, not the echo body.
    """
    bus = EventBus()
    install_dispatcher(bus)

    store = SessionStore(store=MemoryTapeStore(), tapes_dir=tmp_path / "tapes")
    parent = await store.open(tmp_path, "integration-parent")

    # Bundled plugins: wire each directly onto the bus.
    react = ReActStrategy()
    echo = EchoLLM()
    react_ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.strategy-react"),
        config={"provider": "echo", "model": "echo"},
        state_dir=tmp_path / "react",
        plugin_name=react.name,
    )
    (tmp_path / "react").mkdir(parents=True, exist_ok=True)
    await react.on_load(react_ctx)
    echo_ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.llm-echo"),
        config={},
        state_dir=tmp_path / "echo",
        plugin_name=echo.name,
    )
    (tmp_path / "echo").mkdir(parents=True, exist_ok=True)
    await echo.on_load(echo_ctx)
    # No config store here → echo.on_load found zero owned instances.
    # Seed the active set manually so the plugin answers for the
    # `llm-echo` fallback id the strategy resolves to.
    echo._active_instances.add("llm-echo")

    # Subscribe the two bundled plugins directly to the bus with
    # closures that carry the ctx the plugin needs at dispatch time.
    async def _react_handler(ev: Event) -> None:
        await react.on_event(ev, react_ctx)

    async def _echo_handler(ev: Event) -> None:
        await echo.on_event(ev, echo_ctx)

    bus.subscribe("strategy.decide.request", _react_handler, source=react.name)
    bus.subscribe("llm.call.request", _echo_handler, source=echo.name)

    # Bundled agent-tool plugin: registers the `agent` tool and caches
    # the parent session so AgentTool.run can fork it.
    agent = AgentPlugin()
    agent_ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.agent-tool"),
        config={},
        state_dir=tmp_path / "agent",
        plugin_name=agent.name,
        session=parent,
    )
    (tmp_path / "agent").mkdir(parents=True, exist_ok=True)
    await agent.on_load(agent_ctx)

    # Real kernel AgentLoop — subscribes to user.message.received on the
    # bus. When AgentTool.run publishes on the child session, the loop
    # picks it up, runs the ReAct strategy (which asks the echo
    # provider), and emits assistant.message.done on the child.
    loop = AgentLoop(bus, LoopConfig(step_timeout_s=5.0))
    await loop.start()

    results: list[Event] = []
    collect(bus, "tool.call.result", results)

    req = new_event(
        "tool.call.request",
        {
            "id": "integration-call",
            "name": "agent",
            "args": {"goal": "say HELLO and stop", "max_wall_seconds": 5.0},
            "schema_version": "v1",
        },
        session_id="integration-parent",
        source="test",
    )
    await bus.publish(req)

    async def _wait_result() -> Event:
        while not results:
            await asyncio.sleep(0.01)
        return results[0]

    try:
        result_ev = await asyncio.wait_for(_wait_result(), timeout=5.0)
    finally:
        await loop.stop()
        await bus.close()

    envelope = result_ev.payload["envelope"]
    parsed = ToolOk.model_validate(envelope)
    assert parsed.ok is True
    assert parsed.display.kind == "text"
    final_text = parsed.display.text  # type: ignore[union-attr]
    # Echo provider round-tripped: final text is non-empty and carries
    # the echo prefix. Exact-match avoidance documented in the module
    # docstring.
    assert final_text
    assert "HELLO" in final_text or final_text.startswith("(echo)")
