"""Tests for the bash tool plugin.

AC-bindings from ``specs/plugin-tool_bash.spec``:

* argv → ``test_argv_list_runs_and_emits_result``
* invalid cmd → ``test_cmd_not_list_emits_validation_error``
* timeout → ``test_timeout_kills_process_and_emits_timeout``
* filter → ``test_non_bash_tool_name_ignored``
"""

from __future__ import annotations

import logging
from pathlib import Path

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.tool_bash.plugin import BashTool


async def _wire(tmp_path: Path, *, timeout_s: float = 30.0) -> tuple[BashTool, EventBus, KernelContext, list[Event]]:
    plugin = BashTool(timeout_s=timeout_s)
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.tool-bash"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("tool.call.request", _handler, source=plugin.name)

    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("tool.call.result", _observer, source="observer")
    return plugin, bus, ctx, captured


async def test_argv_list_runs_and_emits_result(tmp_path: Path) -> None:
    """Echo runs and its stdout lands in the tool.call.result value."""
    _plugin, bus, _ctx, captured = await _wire(tmp_path)
    req = new_event(
        "tool.call.request",
        {"id": "call-1", "name": "bash", "args": {"cmd": ["echo", "hello"]}},
        session_id="sess-argv",
        source="kernel",
    )
    await bus.publish(req)

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["ok"] is True
    assert payload["id"] == "call-1"
    assert payload["request_id"] == req.id
    assert payload["value"]["returncode"] == 0
    assert "hello" in payload["value"]["stdout"]


async def test_cmd_not_list_emits_validation_error(tmp_path: Path) -> None:
    """A non-list cmd fails validation without spawning anything."""
    _plugin, bus, _ctx, captured = await _wire(tmp_path)
    req = new_event(
        "tool.call.request",
        {"id": "call-bad", "name": "bash", "args": {"cmd": "echo hello"}},
        session_id="sess-bad-argv",
        source="kernel",
    )
    await bus.publish(req)

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["ok"] is False
    assert "argv list" in payload["error"]
    assert payload["request_id"] == req.id


async def test_timeout_kills_process_and_emits_timeout(tmp_path: Path) -> None:
    """A sleep longer than the plugin's timeout is killed; error=timeout."""
    _plugin, bus, _ctx, captured = await _wire(tmp_path, timeout_s=0.2)
    req = new_event(
        "tool.call.request",
        {"id": "call-slow", "name": "bash", "args": {"cmd": ["sleep", "5"]}},
        session_id="sess-timeout",
        source="kernel",
    )
    await bus.publish(req)

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["ok"] is False
    assert payload["error"] == "timeout"
    assert payload["request_id"] == req.id


async def test_non_bash_tool_name_ignored(tmp_path: Path) -> None:
    """A request for another tool does not emit a result from tool-bash."""
    _plugin, bus, _ctx, captured = await _wire(tmp_path)
    await bus.publish(
        new_event(
            "tool.call.request",
            {"id": "call-other", "name": "fs", "args": {"cmd": ["echo", "hi"]}},
            session_id="sess-other",
            source="kernel",
        )
    )
    assert captured == []
