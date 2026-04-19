"""Tests for the agent-tool plugin.

AC-bindings from ``specs/plugin-agent_tool.spec``:

* happy path → ``test_happy_path_subagent_returns_final_text``
* parent tape isolation → ``test_parent_tape_is_immutable_after_child_runs``
* depth guard → ``test_depth_guard_blocks_runaway_recursion``
* timeout → ``test_timeout_returns_tool_error``
* allowlist narrowed → ``test_allowlist_records_forbidden_hits``
* cancellation → ``test_cancellation_emits_failed_event``
* approval default true → ``test_agent_tool_requires_approval_by_default``
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.session import MemoryTapeStore, Session, SessionStore
from yaya.kernel.tool import (
    ToolError,
    ToolOk,
    _clear_tool_registry,
    install_dispatcher,
)
from yaya.plugins.agent_tool.plugin import (
    EVENT_ALLOWLIST_NARROWED,
    EVENT_SUBAGENT_COMPLETED,
    EVENT_SUBAGENT_FAILED,
    EVENT_SUBAGENT_STARTED,
    AgentPlugin,
    AgentTool,
    _Runtime,
)

from ._fake_strategy import FakeAgentLoop, collect

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    """Every test gets a clean v1 tool registry.

    The registry is a module-level singleton; without a reset
    subsequent tests would collide on the ``agent`` name after the
    plugin's ``on_load`` registers it.
    """
    _clear_tool_registry()
    yield
    _clear_tool_registry()
    _Runtime.session = None
    _Runtime.bus = None


async def _bootstrap(
    tmp_path: Path,
    *,
    parent_session_id: str = "parent",
) -> tuple[EventBus, Session, AgentPlugin]:
    """Spin up bus + dispatcher + session store + plugin for a test."""
    bus = EventBus()
    install_dispatcher(bus)
    store = SessionStore(store=MemoryTapeStore(), tapes_dir=tmp_path / "tapes")
    parent = await store.open(tmp_path, parent_session_id)

    plugin = AgentPlugin()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.agent-tool"),
        config={},
        state_dir=tmp_path / "agent",
        plugin_name=plugin.name,
        session=parent,
    )
    (tmp_path / "agent").mkdir(parents=True, exist_ok=True)
    await plugin.on_load(ctx)
    return bus, parent, plugin


async def _publish_and_wait(
    bus: EventBus,
    parent_session_id: str,
    *,
    args: dict[str, Any],
    call_id: str = "call-1",
) -> Event:
    """Publish a v1 ``tool.call.request`` and return the resulting result/error."""
    results: list[Event] = []
    errors: list[Event] = []
    collect(bus, "tool.call.result", results)
    collect(bus, "tool.error", errors)

    req = new_event(
        "tool.call.request",
        {
            "id": call_id,
            "name": "agent",
            "args": args,
            "schema_version": "v1",
        },
        session_id=parent_session_id,
        source="test",
    )
    await bus.publish(req)
    # Give the bus a tick in case nested publishes are still draining.
    for _ in range(5):
        if results or errors:
            break
        await asyncio.sleep(0.01)
    if errors and not results:
        return errors[0]
    assert results, "expected tool.call.result; none captured"
    return results[0]


async def test_agent_tool_requires_approval_by_default() -> None:
    """The contract hard-rule: spawning a sub-agent always prompts the user."""
    assert AgentTool.requires_approval is True
    assert AgentTool.name == "agent"


async def test_happy_path_subagent_returns_final_text(tmp_path: Path) -> None:
    """Fork → child turn → assistant.message.done → ToolOk with final text."""
    bus, _parent, _plugin = await _bootstrap(tmp_path)

    loop = FakeAgentLoop(bus=bus, default_answer="42")
    loop.start()

    started: list[Event] = []
    completed: list[Event] = []
    collect(bus, EVENT_SUBAGENT_STARTED, started)
    collect(bus, EVENT_SUBAGENT_COMPLETED, completed)

    result_ev = await _publish_and_wait(
        bus,
        "parent",
        args={"goal": "what is the answer?"},
    )
    loop.stop()

    envelope = result_ev.payload["envelope"]
    parsed = ToolOk.model_validate(envelope)
    assert parsed.ok is True
    assert parsed.display.kind == "text"
    assert parsed.display.text == "42"  # type: ignore[union-attr]

    # The child session id is a descendant of the parent.
    assert started and completed
    child_id = started[0].payload["child_id"]
    assert child_id.startswith("parent::agent::")
    assert loop.seen_sessions == [child_id]


async def test_parent_tape_is_immutable_after_child_runs(tmp_path: Path) -> None:
    """Lesson #32: child appends never leak back to parent's tape."""
    bus, parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, default_answer="ok")
    loop.start()

    before = await parent.entries()
    before_len = len(before)

    await _publish_and_wait(bus, "parent", args={"goal": "hi"})
    loop.stop()

    after = await parent.entries()
    # The parent tape gains no entries from the child's work; auto-tape
    # persistence is not wired into this test harness, so the parent's
    # length is unchanged.
    assert len(after) == before_len


async def test_depth_guard_blocks_runaway_recursion(tmp_path: Path) -> None:
    """Depth >= max triggers a rejected ToolError before any fork."""
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    # Rebind the runtime session to a deep grandchild (4 hops ≥ default 5? no, 5 hops equals cap).
    # Build a session whose id has _Runtime.max_depth hops so the guard fires.
    from yaya.kernel.session import MemoryTapeStore as _Store
    from yaya.kernel.session import SessionStore as _Store_

    assert _Runtime.max_depth >= 1
    deep_id = "::agent::".join(["root"] + [f"s{i}" for i in range(_Runtime.max_depth)])
    store = _Store_(store=_Store(), tapes_dir=tmp_path / "deep")
    deep_parent = await store.open(tmp_path, deep_id)
    _Runtime.session = deep_parent

    result_ev = await _publish_and_wait(bus, deep_id, args={"goal": "recurse"})
    envelope = result_ev.payload["envelope"]
    parsed = ToolError.model_validate(envelope)
    assert parsed.ok is False
    assert parsed.kind == "rejected"
    assert "max depth" in parsed.brief


async def test_timeout_returns_tool_error(tmp_path: Path) -> None:
    """max_wall_seconds exhaustion surfaces as ToolError(kind=timeout)."""
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, never_done=True)
    loop.start()

    failed: list[Event] = []
    collect(bus, EVENT_SUBAGENT_FAILED, failed)

    result_ev = await _publish_and_wait(
        bus,
        "parent",
        args={"goal": "hang", "max_wall_seconds": 0.05},
    )
    loop.stop()

    envelope = result_ev.payload["envelope"]
    parsed = ToolError.model_validate(envelope)
    assert parsed.ok is False
    assert parsed.kind == "timeout"
    assert failed and failed[0].payload["reason"] == "timeout"


async def test_allowlist_records_forbidden_hits(tmp_path: Path) -> None:
    """A child tool call outside ``tools`` is recorded and surfaces an event."""
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, default_answer="done", tool_before_done="forbidden-tool")
    loop.start()

    narrowed: list[Event] = []
    completed: list[Event] = []
    collect(bus, EVENT_ALLOWLIST_NARROWED, narrowed)
    collect(bus, EVENT_SUBAGENT_COMPLETED, completed)

    result_ev = await _publish_and_wait(
        bus,
        "parent",
        args={"goal": "use forbidden tool", "tools": ["allowed-tool"]},
    )
    loop.stop()

    envelope = result_ev.payload["envelope"]
    assert envelope["ok"] is True
    assert narrowed, "expected x.agent.allowlist.narrowed"
    assert narrowed[0].payload["attempted"] == ["forbidden-tool"]
    assert narrowed[0].payload["allowed"] == ["allowed-tool"]
    assert completed[0].payload["forbidden_tool_hits"] == ["forbidden-tool"]


async def test_allowlist_none_does_not_emit_narrowed(tmp_path: Path) -> None:
    """``tools=None`` means inherit parent — no allowlist tracking."""
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, default_answer="done", tool_before_done="any-tool")
    loop.start()

    narrowed: list[Event] = []
    completed: list[Event] = []
    collect(bus, EVENT_ALLOWLIST_NARROWED, narrowed)
    collect(bus, EVENT_SUBAGENT_COMPLETED, completed)

    await _publish_and_wait(bus, "parent", args={"goal": "pass through"})
    loop.stop()

    assert not narrowed
    assert completed[0].payload["forbidden_tool_hits"] == []


async def test_cancellation_emits_failed_event(tmp_path: Path) -> None:
    """Parent cancel → child reports failed(reason=cancelled) in finally.

    The v1 dispatcher wraps :meth:`AgentTool.run` in a try/except for
    broad :class:`Exception`; :class:`asyncio.CancelledError` bypasses
    that and propagates, letting the tool's own ``finally`` emit the
    failure event before the cancellation unwinds.
    """
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, never_done=True)
    loop.start()

    failed: list[Event] = []
    collect(bus, EVENT_SUBAGENT_FAILED, failed)

    # Drive AgentTool.run directly so we own the task and can cancel it.
    tool = AgentTool(goal="hang", max_wall_seconds=10.0)
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.agent-tool"),
        config={},
        state_dir=tmp_path / "agent-cancel",
        plugin_name="agent-tool",
    )
    (tmp_path / "agent-cancel").mkdir(parents=True, exist_ok=True)

    task = asyncio.create_task(tool.run(ctx))
    # Let the task subscribe and publish user.message.received.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the bus a tick to deliver the failed event emitted in finally.
    for _ in range(10):
        if failed:
            break
        await asyncio.sleep(0.01)
    loop.stop()

    assert failed, "expected x.agent.subagent.failed on cancel"
    assert failed[0].payload["reason"] == "cancelled"


async def test_unbound_runtime_returns_internal_error(tmp_path: Path) -> None:
    """If plugin.on_load never ran, AgentTool.run surfaces ToolError(kind=internal).

    Registers the tool class directly so the dispatcher finds it, but
    leaves ``_Runtime`` unbound (simulating a kernel boot where the
    plugin was registered but its ``on_load`` crashed before binding
    the runtime session/bus).
    """
    from yaya.kernel.tool import register_tool

    bus = EventBus()
    install_dispatcher(bus)
    register_tool(AgentTool)
    _Runtime.bus = None
    _Runtime.session = None

    result_ev = await _publish_and_wait(bus, "parent", args={"goal": "nope"})
    envelope = result_ev.payload["envelope"]
    parsed = ToolError.model_validate(envelope)
    assert parsed.kind == "internal"
    assert "not bound" in parsed.brief


async def test_extension_events_attributed_to_plugin(tmp_path: Path) -> None:
    """``x.agent.*`` events must carry ``source="agent-tool"``.

    The ctx handed to :meth:`AgentTool.run` by the v1 dispatcher stamps
    ``source="kernel"`` (see ``kernel/tool.py::install_dispatcher``);
    the plugin caches its own :class:`KernelContext` on ``on_load`` so
    plugin-private events are attributed correctly. Asserted on the
    ``x.agent.subagent.started`` emit — the first one of the four —
    so a future regression trips immediately.
    """
    bus, _parent, _plugin = await _bootstrap(tmp_path)
    loop = FakeAgentLoop(bus=bus, default_answer="done")
    loop.start()

    started: list[Event] = []
    completed: list[Event] = []
    collect(bus, EVENT_SUBAGENT_STARTED, started)
    collect(bus, EVENT_SUBAGENT_COMPLETED, completed)

    await _publish_and_wait(bus, "parent", args={"goal": "attribution check"})
    loop.stop()

    assert started and completed
    assert started[0].source == "agent-tool"
    assert completed[0].source == "agent-tool"


async def test_registered_after_on_load(tmp_path: Path) -> None:
    """``on_load`` registers the v1 tool under name ``agent``."""
    from yaya.kernel.tool import get_tool

    await _bootstrap(tmp_path)
    assert get_tool("agent") is AgentTool
