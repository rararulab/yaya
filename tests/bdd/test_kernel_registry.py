"""Pytest-bdd execution of specs/kernel-registry.spec scenarios.

The Gherkin text in ``features/kernel-registry.feature`` is the
authoritative BDD contract for the kernel plugin registry. Each
scenario binds to step definitions in this module via pytest-bdd;
changing the scenario text without a matching step def causes pytest
to fail with ``StepDefinitionNotFoundError``.

This complements (does not replace) the engineering-level tests in
``tests/kernel/test_registry.py``. BDD here proves the scenarios the
spec advertises are actually executed; the pytest unit tests cover
edge cases and internals not worth surfacing in Gherkin.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from pytest_bdd import given, scenarios, then, when

# Production-code imports only. Step defs wire Gherkin text to the real
# API; they do NOT import test helpers from tests/kernel/*.
from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import Category, KernelContext
from yaya.kernel.registry import PluginRegistry, PluginStatus

from .conftest import BDDContext

pytestmark = pytest.mark.unit

FEATURE_FILE = Path(__file__).parent / "features" / "kernel-registry.feature"
scenarios(str(FEATURE_FILE))


# ---------------------------------------------------------------------------
# Plugin / entry-point stubs (duplicated here rather than imported from
# tests/kernel/test_registry.py per the BDD workflow rules).
# ---------------------------------------------------------------------------


class _RecordingPlugin:
    """Minimal ``Plugin``-conforming stub that records lifecycle calls."""

    name = "stub-tool"
    version = "0.1.0"
    category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self, *, subscribes: list[str] | None = None) -> None:
        self._subs = subscribes if subscribes is not None else ["tool.call.request"]
        self.on_load_calls = 0
        self.on_unload_calls = 0
        self.events: list[Event] = []

    def subscriptions(self) -> list[str]:
        return list(self._subs)

    async def on_load(self, ctx: KernelContext) -> None:
        self.on_load_calls += 1

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        self.events.append(ev)

    async def on_unload(self, ctx: KernelContext) -> None:
        self.on_unload_calls += 1


class _FailingPlugin:
    """Plugin whose ``on_event`` raises every time."""

    name = "flaky-tool"
    version = "0.2.0"
    category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.on_unload_calls = 0

    def subscriptions(self) -> list[str]:
        return ["tool.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        return None

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        raise RuntimeError("bdd failure")

    async def on_unload(self, ctx: KernelContext) -> None:
        self.on_unload_calls += 1


class _ToggleFailPlugin:
    """Plugin whose ``on_event`` alternates between raising and succeeding."""

    name = "toggle-tool"
    version = "0.1.0"
    category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.fail = True
        self.on_unload_calls = 0

    def subscriptions(self) -> list[str]:
        return ["tool.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        return None

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        if self.fail:
            raise RuntimeError("toggled failure")

    async def on_unload(self, ctx: KernelContext) -> None:
        self.on_unload_calls += 1


class _FakeDist:
    def __init__(self, dist_name: str) -> None:
        self.metadata = {"Name": dist_name}


class _FakeEntryPoint:
    """Duck-typed stand-in for :class:`importlib.metadata.EntryPoint`."""

    def __init__(self, name: str, obj: Any, *, bundled: bool = False) -> None:
        self.name = name
        self._obj = obj
        self.dist = _FakeDist("yaya" if bundled else "third-party-pkg")

    def load(self) -> Any:
        return self._obj


def _fake_entry_points(eps: list[_FakeEntryPoint]) -> Callable[[str], list[_FakeEntryPoint]]:
    def _factory(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return eps

    return _factory


async def _drain_until(predicate: Callable[[], bool]) -> None:
    """Yield control until ``predicate()`` is true (or we give up)."""
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("predicate never became true")


def _collector(bucket: list[Event]) -> Callable[[Event], Any]:
    async def _handler(ev: Event) -> None:
        bucket.append(ev)

    return _handler


# ---------------------------------------------------------------------------
# Scenario 1 — entry-point discovery loads a plugin and emits plugin.loaded
# ---------------------------------------------------------------------------


@given("a Plugin object exposed via a yaya.plugins.v1 entry point")
def _plugin_via_entry_point(ctx: BDDContext, tmp_path: Path) -> None:
    plugin = _RecordingPlugin()
    ctx.extras["plugin"] = plugin
    ctx.extras["tmp_path"] = tmp_path
    ctx.extras["loaded_events"] = []
    ctx.extras["entry_points"] = [_FakeEntryPoint("stub", plugin)]


@when("the registry is started and entry-point discovery runs")
def _start_registry_discovery(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    bus = EventBus()
    ctx.bus = bus
    loaded: list[Event] = ctx.extras["loaded_events"]
    bus.subscribe("plugin.loaded", _collector(loaded), source="bdd-observer")

    eps = ctx.extras["entry_points"]
    patcher = patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points(eps))
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=ctx.extras["tmp_path"])
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@then("the plugin's on_load is called exactly once")
def _on_load_called_once(ctx: BDDContext) -> None:
    plugin: _RecordingPlugin = ctx.extras["plugin"]
    assert plugin.on_load_calls == 1, f"expected 1 on_load call, got {plugin.on_load_calls}"


@then("a plugin.loaded event is emitted carrying its name, version, and category")
def _plugin_loaded_event_payload(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    loaded: list[Event] = ctx.extras["loaded_events"]
    assert len(loaded) >= 1
    payload = loaded[-1].payload
    plugin: _RecordingPlugin = ctx.extras["plugin"]
    assert payload == {
        "name": plugin.name,
        "version": plugin.version,
        "category": str(plugin.category),
    }
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 2 — bundled plugin uses the same load code path as third-party
# ---------------------------------------------------------------------------


@given("one bundled and one third-party plugin registered under the same entry-point group")
def _bundled_and_third_party(ctx: BDDContext, tmp_path: Path) -> None:
    bundled = _RecordingPlugin()
    bundled.name = "bundled-a"
    third = _RecordingPlugin()
    third.name = "third-b"
    ctx.extras["bundled"] = bundled
    ctx.extras["third"] = third
    ctx.extras["tmp_path"] = tmp_path
    # Declare third-party first on purpose — the registry must still sort
    # bundled ahead of third-party in snapshot() regardless of declaration.
    ctx.extras["entry_points"] = [
        _FakeEntryPoint("third", third, bundled=False),
        _FakeEntryPoint("bundled", bundled, bundled=True),
    ]


@when("the registry is started")
def _start_registry(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    bus = EventBus()
    ctx.bus = bus
    eps = ctx.extras["entry_points"]
    patcher = patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points(eps))
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=ctx.extras["tmp_path"])
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@then("both plugins run through the identical _load_entry_point code path with no behavioral branch")
def _both_plugins_loaded(ctx: BDDContext) -> None:
    bundled: _RecordingPlugin = ctx.extras["bundled"]
    third: _RecordingPlugin = ctx.extras["third"]
    # Parity proof: both received on_load exactly once. If there were a
    # behavioral branch for bundled, one would diverge.
    assert bundled.on_load_calls == 1
    assert third.on_load_calls == 1


@then("the bundled plugin appears first in snapshot() as the deterministic load-order tie-breaker")
def _bundled_first_in_snapshot(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    names = [row["name"] for row in registry.snapshot()]
    assert names == ["bundled-a", "third-b"], f"got snapshot order: {names}"
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 3 — repeated plugin.error failures past threshold unload the plugin
# ---------------------------------------------------------------------------


@given("a plugin whose on_event raises every time")
def _always_raising_plugin(ctx: BDDContext, tmp_path: Path) -> None:
    plugin = _FailingPlugin()
    ctx.extras["plugin"] = plugin
    ctx.extras["tmp_path"] = tmp_path
    ctx.extras["removed_events"] = []
    ctx.extras["entry_points"] = [_FakeEntryPoint("flaky", plugin)]


@given("a failure threshold default of 3 consecutive plugin.error events")
def _threshold_three(ctx: BDDContext) -> None:
    ctx.extras["failure_threshold"] = 3


@when("three subscribed events are delivered to that plugin")
def _deliver_three_events(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    bus = EventBus(handler_timeout_s=1.0)
    ctx.bus = bus
    removed: list[Event] = ctx.extras["removed_events"]
    bus.subscribe("plugin.removed", _collector(removed), source="bdd-observer")

    eps = ctx.extras["entry_points"]
    patcher = patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points(eps))
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(
        bus,
        state_dir=ctx.extras["tmp_path"],
        failure_threshold=ctx.extras["failure_threshold"],
    )
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())

    async def _fire_all() -> None:
        for i in range(3):
            await bus.publish(
                new_event(
                    "tool.call.request",
                    {"id": f"call-{i}", "name": "noop", "args": {}},
                    session_id="s",
                    source="kernel",
                )
            )
        await _drain_until(lambda: len(removed) >= 1)

    loop.run_until_complete(_fire_all())


@then("the registry unloads the plugin and emits plugin.removed")
def _plugin_removed_emitted(ctx: BDDContext) -> None:
    removed: list[Event] = ctx.extras["removed_events"]
    assert len(removed) == 1
    assert removed[0].payload == {"name": "flaky-tool"}


@then("the plugin's on_unload is called exactly once")
def _on_unload_called_once(ctx: BDDContext) -> None:
    plugin: _FailingPlugin = ctx.extras["plugin"]
    assert plugin.on_unload_calls == 1


@then('its status in snapshot() becomes "failed"')
def _status_failed(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    statuses = {row["name"]: row["status"] for row in registry.snapshot()}
    assert statuses["flaky-tool"] == "failed"
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 4 — consecutive failure counter resets on a successful on_event
# ---------------------------------------------------------------------------


@given("a plugin registered with the default consecutive failure threshold")
def _plugin_with_default_threshold(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    plugin = _ToggleFailPlugin()
    ctx.extras["plugin"] = plugin
    ctx.extras["tmp_path"] = tmp_path
    ctx.extras["errors"] = []
    ctx.extras["removed_events"] = []

    bus = EventBus(handler_timeout_s=1.0)
    ctx.bus = bus
    bus.subscribe("plugin.error", _collector(ctx.extras["errors"]), source="bdd-observer")
    bus.subscribe("plugin.removed", _collector(ctx.extras["removed_events"]), source="bdd-observer")

    patcher = patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("toggle", plugin)]),
    )
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=3)
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@given("two plugin.error events already attributed to that loaded plugin")
def _two_errors_recorded(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    plugin: _ToggleFailPlugin = ctx.extras["plugin"]
    bus = ctx.bus
    assert bus is not None
    plugin.fail = True

    async def _fire_two() -> None:
        for _ in range(2):
            await bus.publish(
                new_event(
                    "tool.call.request",
                    {"id": "x", "name": "noop", "args": {}},
                    session_id="s",
                    source="kernel",
                )
            )
        await _drain_until(lambda: len(ctx.extras["errors"]) >= 2)

    loop.run_until_complete(_fire_two())
    registry: PluginRegistry = ctx.extras["registry"]
    assert registry._records["toggle-tool"].error_count == 2


@when("a subsequent on_event invocation succeeds")
def _successful_event(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    plugin: _ToggleFailPlugin = ctx.extras["plugin"]
    bus = ctx.bus
    assert bus is not None
    plugin.fail = False
    registry: PluginRegistry = ctx.extras["registry"]

    async def _fire_success() -> None:
        await bus.publish(
            new_event(
                "tool.call.request",
                {"id": "ok", "name": "noop", "args": {}},
                session_id="s",
                source="kernel",
            )
        )
        await _drain_until(lambda: registry._records["toggle-tool"].error_count == 0)

    loop.run_until_complete(_fire_success())


@then("the consecutive failure counter resets to zero")
def _counter_reset(ctx: BDDContext) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    assert registry._records["toggle-tool"].error_count == 0


@then("the plugin stays loaded despite a later isolated plugin.error")
def _stays_loaded_after_isolated_error(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    plugin: _ToggleFailPlugin = ctx.extras["plugin"]
    bus = ctx.bus
    assert bus is not None
    registry: PluginRegistry = ctx.extras["registry"]
    errors: list[Event] = ctx.extras["errors"]
    removed: list[Event] = ctx.extras["removed_events"]

    plugin.fail = True

    async def _fire_one() -> None:
        await bus.publish(
            new_event(
                "tool.call.request",
                {"id": "y", "name": "noop", "args": {}},
                session_id="s",
                source="kernel",
            )
        )
        await _drain_until(lambda: len(errors) >= 3)

    loop.run_until_complete(_fire_one())

    assert not removed, "plugin must not have been unloaded after a single post-reset error"
    assert registry._records["toggle-tool"].status is PluginStatus.LOADED
    assert registry._records["toggle-tool"].error_count == 1
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 5 — snapshot returns one entry per registered plugin
# ---------------------------------------------------------------------------


@given("two plugins that load successfully and one that fails on_load")
def _mixed_plugins(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    good_a = _RecordingPlugin()
    good_a.name = "tool-a"
    good_b = _RecordingPlugin()
    good_b.name = "tool-b"

    class _BrokenLoader(_RecordingPlugin):
        name = "broken"

        async def on_load(self, kctx: KernelContext) -> None:
            raise RuntimeError("refuse")

    broken = _BrokenLoader()

    eps = [
        _FakeEntryPoint("a", good_a),
        _FakeEntryPoint("b", good_b),
        _FakeEntryPoint("c", broken),
    ]

    bus = EventBus()
    ctx.bus = bus
    patcher = patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points(eps))
    patcher.start()
    ctx.extras["patchers"] = [patcher]
    registry = PluginRegistry(bus, state_dir=tmp_path)
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@when("registry.snapshot() is called")
def _call_snapshot(ctx: BDDContext) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    ctx.extras["snapshot_rows"] = registry.snapshot()


@then("three entries are returned carrying name, version, category, status fields in first-seen load order")
def _three_entries_with_fields(ctx: BDDContext) -> None:
    rows = ctx.extras["snapshot_rows"]
    assert len(rows) == 3
    for row in rows:
        assert set(row.keys()) == {"name", "version", "category", "status"}
    assert [r["name"] for r in rows] == ["tool-a", "tool-b", "broken"]


@then('the failing plugin\'s status is "failed"')
def _failing_status_failed(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    rows = ctx.extras["snapshot_rows"]
    by_name = {r["name"]: r for r in rows}
    assert by_name["broken"]["status"] == "failed"
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 6 — install shells to uv pip via subprocess_exec and re-runs discovery
# ---------------------------------------------------------------------------


@given("an uv binary available on PATH")
def _uv_on_path(ctx: BDDContext, tmp_path: Path) -> None:
    ctx.extras["tmp_path"] = tmp_path
    ctx.extras["uv_path"] = "/usr/bin/uv"


@when('registry.install("yaya-tool-bash") is called')
def _install_yaya_tool_bash(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    plugin = _RecordingPlugin()
    ctx.extras["plugin"] = plugin

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    # First discovery (start) returns []; post-install discovery returns the plugin.
    discovered: list[list[_FakeEntryPoint]] = [[], [_FakeEntryPoint("stub", plugin)]]

    def _eps_stub(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return discovered.pop(0) if discovered else []

    stack = ExitStack()
    mock_exec = stack.enter_context(
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        )
    )
    stack.enter_context(patch("yaya.kernel.registry.entry_points", side_effect=_eps_stub))
    stack.enter_context(patch("yaya.kernel.registry.shutil.which", return_value=ctx.extras["uv_path"]))
    ctx.extras["stack"] = stack

    bus = EventBus()
    ctx.bus = bus
    registry = PluginRegistry(bus, state_dir=ctx.extras["tmp_path"])
    ctx.extras["registry"] = registry

    async def _run() -> None:
        await registry.start()
        assert registry.snapshot() == []
        await registry.install("yaya-tool-bash")

    loop.run_until_complete(_run())
    ctx.extras["mock_exec"] = mock_exec


@then('asyncio.create_subprocess_exec is invoked with "uv pip install yaya-tool-bash"')
def _subprocess_exec_called(ctx: BDDContext) -> None:
    mock_exec = ctx.extras["mock_exec"]
    mock_exec.assert_awaited_once()
    argv = mock_exec.call_args.args
    assert argv[0] == ctx.extras["uv_path"]
    assert "pip" in argv and "install" in argv and "yaya-tool-bash" in argv


@then("entry-point discovery re-runs so the freshly installed plugin comes online")
def _discovery_reran(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    plugin: _RecordingPlugin = ctx.extras["plugin"]
    registry: PluginRegistry = ctx.extras["registry"]
    assert plugin.on_load_calls == 1
    assert any(row["name"] == plugin.name for row in registry.snapshot())
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 7 — install source validation rejects unsupported scheme
# ---------------------------------------------------------------------------


@given('a source string with an unsupported URL scheme like "git+ssh"')
def _hazardous_source(ctx: BDDContext, tmp_path: Path) -> None:
    # Shell-metachar filtering is deliberately NOT in scope — safety
    # comes from ``create_subprocess_exec`` (no shell). The validator's
    # surviving job is rejecting unsupported source SHAPES.
    ctx.extras["source"] = "git+ssh://example.com/foo.git"
    ctx.extras["tmp_path"] = tmp_path


@when("registry.install(source) is called")
def _install_hazardous(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    bus = EventBus()
    ctx.bus = bus
    # Stub entry_points so start() cannot blow up on the real process.
    patcher = patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([]),
    )
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    # Guard against any subprocess ever being spawned; assertion target
    # for the next Then.
    exec_mock = AsyncMock()
    subproc_patcher = patch(
        "yaya.kernel.registry.asyncio.create_subprocess_exec",
        exec_mock,
    )
    subproc_patcher.start()
    ctx.extras["patchers"].append(subproc_patcher)
    ctx.extras["exec_mock"] = exec_mock

    registry = PluginRegistry(bus, state_dir=ctx.extras["tmp_path"])
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())

    try:
        loop.run_until_complete(registry.install(ctx.extras["source"]))
    except ValueError as exc:
        ctx.extras["raised"] = exc
    except Exception as exc:  # pragma: no cover - would indicate a bug
        ctx.extras["raised"] = exc


@then("ValueError is raised by source validation before any subprocess is spawned")
def _value_error_before_subprocess(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    raised = ctx.extras.get("raised")
    assert isinstance(raised, ValueError), f"expected ValueError, got {type(raised).__name__}: {raised!r}"
    # No subprocess was ever spawned — validation intercepted the call.
    exec_mock: AsyncMock = ctx.extras["exec_mock"]
    exec_mock.assert_not_called()
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 8 — remove refuses to uninstall a bundled plugin
# ---------------------------------------------------------------------------


@given("a bundled plugin whose bundled membership is re-derived from entry-point metadata")
def _bundled_plugin_registered(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    plugin = _RecordingPlugin()
    plugin.name = "bundled-web"
    ctx.extras["plugin"] = plugin

    bus = EventBus()
    ctx.bus = bus
    patcher = patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("web", plugin, bundled=True)]),
    )
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=tmp_path)
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@when('registry.remove("<bundled-name>") is called')
def _remove_bundled(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    try:
        loop.run_until_complete(registry.remove("bundled-web"))
    except ValueError as exc:
        ctx.extras["raised"] = exc


@then('ValueError is raised referencing "bundled" at the enforcement point')
def _value_error_bundled(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    raised = ctx.extras.get("raised")
    assert isinstance(raised, ValueError), f"expected ValueError, got {type(raised).__name__}: {raised!r}"
    assert "bundled" in str(raised).lower()
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Scenario 9 — stop runs on_unload in reverse load order and emits kernel.shutdown
# ---------------------------------------------------------------------------


@given("two loaded plugins registered in order A then B")
def _two_plugins_a_then_b(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    calls: list[str] = []
    ctx.extras["unload_calls"] = calls

    def _make(name: str) -> _RecordingPlugin:
        p = _RecordingPlugin()
        p.name = name
        orig = p.on_unload

        async def _tracked(kctx: KernelContext) -> None:
            calls.append(name)
            await orig(kctx)

        # Monkey-patching a Plugin instance method to record unload order;
        # mypy correctly forbids method reassignment, but the test owns this
        # instance and the swap is the whole point.
        p.on_unload = _tracked  # type: ignore[method-assign]
        return p

    a = _make("a")
    b = _make("b")

    bus = EventBus()
    ctx.bus = bus
    shutdown_events: list[Event] = []
    ctx.extras["shutdown_events"] = shutdown_events
    bus.subscribe("kernel.shutdown", _collector(shutdown_events), source="bdd-observer")

    patcher = patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("a", a), _FakeEntryPoint("b", b)]),
    )
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=tmp_path)
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@when("registry.stop() is called")
def _call_stop(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    loop.run_until_complete(registry.stop())
    ctx.extras["stopped"] = True


@then("on_unload is invoked on B before A in reverse load order")
def _reverse_unload_order(ctx: BDDContext) -> None:
    calls: list[str] = ctx.extras["unload_calls"]
    assert calls == ["b", "a"], f"got call order: {calls}"


@then("a kernel.shutdown event is emitted once every loaded plugin has unloaded")
def _kernel_shutdown_emitted(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    shutdown: list[Event] = ctx.extras["shutdown_events"]
    assert len(shutdown) == 1
    assert shutdown[0].payload == {"reason": "stop"}
    _teardown(ctx, loop, already_stopped=True)


# ---------------------------------------------------------------------------
# Scenario 10 — concurrent plugin.error events past threshold trigger a single unload task
# ---------------------------------------------------------------------------


@given("a loaded plugin whose consecutive failure counter is one below threshold")
def _plugin_one_below_threshold(ctx: BDDContext, tmp_path: Path, loop: asyncio.AbstractEventLoop) -> None:
    # threshold=1 so every ``plugin.error`` is "one below threshold when
    # zero errors have been counted yet, and the first arriving error
    # breaches the threshold". With 10 concurrent sessions below, the
    # registry must still spawn exactly one unload task.
    plugin = _FailingPlugin()
    ctx.extras["plugin"] = plugin
    ctx.extras["removed_events"] = []

    bus = EventBus(handler_timeout_s=1.0)
    ctx.bus = bus
    bus.subscribe("plugin.removed", _collector(ctx.extras["removed_events"]), source="bdd-observer")

    patcher = patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("flaky", plugin)]),
    )
    patcher.start()
    ctx.extras["patchers"] = [patcher]

    registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=1)
    ctx.extras["registry"] = registry
    loop.run_until_complete(registry.start())


@when("several concurrent plugin.error events for that plugin arrive in the same tick")
def _concurrent_errors(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    bus = ctx.bus
    assert bus is not None
    removed: list[Event] = ctx.extras["removed_events"]

    async def _fan_out() -> None:
        await asyncio.gather(
            *(
                bus.publish(
                    new_event(
                        "tool.call.request",
                        {"id": f"c-{i}", "name": "noop", "args": {}},
                        session_id=f"s-{i}",
                        source="kernel",
                    )
                )
                for i in range(10)
            )
        )
        await _drain_until(lambda: len(removed) >= 1)
        # Let any rival unload attempts (if the synchronous flip were
        # missing) finish executing before we assert.
        await asyncio.sleep(0.05)

    loop.run_until_complete(_fan_out())


@then("the registry claims the transient unloading status synchronously and schedules exactly one unload task")
def _single_unload_task(ctx: BDDContext) -> None:
    plugin: _FailingPlugin = ctx.extras["plugin"]
    removed: list[Event] = ctx.extras["removed_events"]
    assert plugin.on_unload_calls == 1, (
        f"expected exactly 1 on_unload call, got {plugin.on_unload_calls} — "
        "rival plugin.error events spawned duplicate unload tasks"
    )
    assert len(removed) == 1, f"expected exactly 1 plugin.removed event, got {len(removed)}"


@then("rival handlers observe the flip and short-circuit")
def _rivals_short_circuit(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    registry: PluginRegistry = ctx.extras["registry"]
    statuses = {row["name"]: row["status"] for row in registry.snapshot()}
    assert statuses["flaky-tool"] == "failed"
    _teardown(ctx, loop)


# ---------------------------------------------------------------------------
# Shared teardown.
# ---------------------------------------------------------------------------


def _teardown(
    ctx: BDDContext,
    loop: asyncio.AbstractEventLoop,
    *,
    already_stopped: bool = False,
) -> None:
    """Stop the registry, close the bus, and undo any patches.

    Called from the final ``@then`` of each scenario so the per-scenario
    event loop fixture tears down cleanly. ``already_stopped`` lets the
    stop-order scenario skip a redundant ``registry.stop()``.
    """
    registry: PluginRegistry | None = ctx.extras.get("registry")
    bus: EventBus | None = ctx.bus

    async def _shutdown() -> None:
        if registry is not None and not already_stopped:
            await registry.stop()
        if bus is not None:
            await bus.close()

    try:
        loop.run_until_complete(_shutdown())
    finally:
        stack = ctx.extras.get("stack")
        if stack is not None:
            stack.close()
        for patcher in ctx.extras.get("patchers", []):
            patcher.stop()
