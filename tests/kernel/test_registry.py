"""Tests for the kernel plugin registry.

AC-bindings from ``specs/kernel-registry.spec``:

* AC-01 entry-point discovery → ``test_entry_point_discovery_loads_plugin``
* AC-02 bundled uses same path → ``test_bundled_plugin_uses_same_load_path``
* AC-03 repeated failures unload → ``test_repeated_failures_unload_plugin``
* AC-04 snapshot → ``test_snapshot_lists_every_plugin_with_status``

Extras cover ``install``, ``remove`` bundled-guard, ``stop`` clean
shutdown, and ``validate_install_source``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import Category, KernelContext
from yaya.kernel.registry import (
    PluginRegistry,
    PluginStatus,
    validate_install_source,
)

# ---------------------------------------------------------------------------
# Stub plugins.
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
    """Plugin whose ``on_event`` raises every time — used to trip failure unload."""

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
        raise RuntimeError("stub failure")

    async def on_unload(self, ctx: KernelContext) -> None:
        self.on_unload_calls += 1


class _FakeEntryPoint:
    """Duck-typed stand-in for :class:`importlib.metadata.EntryPoint`.

    The registry uses only ``.name``, ``.load()``, and ``.dist.metadata['Name']``
    so we only need to satisfy those. Production code gets real
    ``EntryPoint`` instances from ``importlib.metadata.entry_points``.
    """

    def __init__(self, name: str, obj: Any, *, bundled: bool = False) -> None:
        self.name = name
        self._obj = obj
        self.dist = _FakeDist("yaya" if bundled else "third-party-pkg")

    def load(self) -> Any:
        return self._obj


class _FakeDist:
    def __init__(self, dist_name: str) -> None:
        self.metadata = {"Name": dist_name}


def _fake_entry_points(eps: list[_FakeEntryPoint]) -> Any:
    """Build a callable mimicking ``importlib.metadata.entry_points(group=...)``."""

    def _factory(group: str) -> list[_FakeEntryPoint]:
        _ = group  # arg is asserted by the registry; value irrelevant to the stub.
        return eps

    return _factory


# ---------------------------------------------------------------------------
# AC-bound tests.
# ---------------------------------------------------------------------------


async def test_entry_point_discovery_loads_plugin(tmp_path: Path) -> None:
    """AC-01 — registry loads a Plugin from a yaya.plugins.v1 entry point."""
    bus = EventBus()
    loaded: list[Event] = []
    bus.subscribe("plugin.loaded", _collector(loaded), source="observer")

    plugin = _RecordingPlugin()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("stub", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    assert plugin.on_load_calls == 1
    assert len(loaded) == 1
    payload = loaded[0].payload
    assert payload == {"name": "stub-tool", "version": "0.1.0", "category": "tool"}

    await registry.stop()
    await bus.close()


async def test_bundled_plugin_uses_same_load_path(tmp_path: Path) -> None:
    """AC-02 — bundled plugins hit the same code path; only load order differs.

    We assert by side-effect: both plugins receive ``on_load`` and emit
    ``plugin.loaded``, and the bundled plugin appears first in the snapshot.
    If the registry had a special case for bundled plugins, either the
    order or the behavioral parity would break.
    """
    bus = EventBus()
    plugin_bundled = _RecordingPlugin()
    plugin_bundled.name = "bundled-a"
    plugin_third = _RecordingPlugin()
    plugin_third.name = "third-b"

    eps = [
        # Declare third-party first to prove sorting, not declaration order, wins.
        _FakeEntryPoint("third", plugin_third, bundled=False),
        _FakeEntryPoint("bundled", plugin_bundled, bundled=True),
    ]
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points(eps),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    assert plugin_bundled.on_load_calls == 1
    assert plugin_third.on_load_calls == 1
    names = [row["name"] for row in registry.snapshot()]
    assert names == ["bundled-a", "third-b"]

    await registry.stop()
    await bus.close()


async def test_repeated_failures_unload_plugin(tmp_path: Path) -> None:
    """AC-03 — threshold of 3 consecutive plugin.error events triggers unload.

    Published ``tool.call.request`` events trip the plugin's ``on_event``
    to raise, which the bus converts into ``plugin.error``. On the third
    error the registry's accounting handler should schedule an unload
    task; we wait until ``plugin.removed`` surfaces.
    """
    bus = EventBus(handler_timeout_s=1.0)
    removed: list[Event] = []
    bus.subscribe("plugin.removed", _collector(removed), source="observer")

    plugin = _FailingPlugin()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("flaky", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=3)
        await registry.start()

    # Fire three ``tool.call.request`` events — each raises, producing a
    # ``plugin.error`` that the registry counts.
    for i in range(3):
        await bus.publish(
            new_event(
                "tool.call.request",
                {"id": f"call-{i}", "name": "noop", "args": {}},
                session_id="s",
                source="kernel",
            )
        )
    # Drain: plugin.error events + registry's unload task + its plugin.removed.
    await asyncio.wait_for(_drain_until(lambda: len(removed) >= 1, bus), timeout=1.0)

    assert len(removed) == 1
    assert removed[0].payload == {"name": "flaky-tool"}
    assert plugin.on_unload_calls == 1
    statuses = {row["name"]: row["status"] for row in registry.snapshot()}
    assert statuses["flaky-tool"] == "failed"

    await registry.stop()
    await bus.close()


async def test_snapshot_lists_every_plugin_with_status(tmp_path: Path) -> None:
    """AC-04 — snapshot returns one row per plugin with name/version/category/status."""
    bus = EventBus()
    good_a = _RecordingPlugin()
    good_a.name = "tool-a"
    good_b = _RecordingPlugin()
    good_b.name = "tool-b"

    class _OnLoadRaises(_RecordingPlugin):
        name = "broken"

        async def on_load(self, ctx: KernelContext) -> None:
            raise RuntimeError("refuse")

    broken = _OnLoadRaises()

    eps = [
        _FakeEntryPoint("a", good_a),
        _FakeEntryPoint("b", good_b),
        _FakeEntryPoint("c", broken),
    ]
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points(eps),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    rows = registry.snapshot()
    assert len(rows) == 3
    by_name = {r["name"]: r for r in rows}
    assert by_name["tool-a"]["status"] == "loaded"
    assert by_name["tool-b"]["status"] == "loaded"
    assert by_name["broken"]["status"] == "failed"
    # Every row has the mandated keys.
    for row in rows:
        assert set(row.keys()) == {"name", "version", "category", "status"}

    await registry.stop()
    await bus.close()


# ---------------------------------------------------------------------------
# Extras — install / remove / stop / validation.
# ---------------------------------------------------------------------------


async def test_install_invokes_subprocess_and_refreshes(tmp_path: Path) -> None:
    """``install`` shells to pip via create_subprocess_exec, then re-discovers."""
    bus = EventBus()
    plugin = _RecordingPlugin()

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    discovered: list[list[_FakeEntryPoint]] = [[], [_FakeEntryPoint("stub", plugin)]]

    def _entry_points_stub(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return discovered.pop(0) if discovered else []

    with (
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        ) as mock_exec,
        patch("yaya.kernel.registry.entry_points", side_effect=_entry_points_stub),
        patch("yaya.kernel.registry.shutil.which", return_value="/usr/bin/uv"),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()  # first discovery returns []
        assert registry.snapshot() == []

        result = await registry.install("yaya-tool-bash")

    assert result == "yaya-tool-bash"
    mock_exec.assert_awaited_once()
    argv = mock_exec.call_args.args
    assert argv[0] == "/usr/bin/uv"
    assert "pip" in argv and "install" in argv and "yaya-tool-bash" in argv
    assert plugin.on_load_calls == 1

    await registry.stop()
    await bus.close()


async def test_install_raises_on_nonzero_exit(tmp_path: Path) -> None:
    """A failing ``uv pip install`` surfaces a RuntimeError with stderr context."""
    bus = EventBus()

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b"boom: resolution failed"))
    fake_proc.returncode = 2

    with (
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        ),
        patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points([])),
        patch("yaya.kernel.registry.shutil.which", return_value="/usr/bin/uv"),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        with pytest.raises(RuntimeError, match="resolution failed"):
            await registry.install("yaya-tool-bash")

    await registry.stop()
    await bus.close()


async def test_remove_bundled_plugin_raises(tmp_path: Path) -> None:
    """Bundled plugins cannot be uninstalled via the registry."""
    bus = EventBus()
    plugin = _RecordingPlugin()
    plugin.name = "bundled-web"
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("web", plugin, bundled=True)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    with pytest.raises(ValueError, match="bundled"):
        await registry.remove("bundled-web")

    await registry.stop()
    await bus.close()


async def test_stop_runs_on_unload_in_reverse_order(tmp_path: Path) -> None:
    """``stop`` invokes ``on_unload`` on every loaded plugin and emits kernel.shutdown."""
    bus = EventBus()
    shutdown_events: list[Event] = []
    bus.subscribe("kernel.shutdown", _collector(shutdown_events), source="observer")

    calls: list[str] = []

    def _make(name: str) -> _RecordingPlugin:
        p = _RecordingPlugin()
        p.name = name
        orig = p.on_unload

        async def _tracked(ctx: KernelContext) -> None:
            calls.append(name)
            await orig(ctx)

        p.on_unload = _tracked  # type: ignore[method-assign]
        return p

    a = _make("a")
    b = _make("b")
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([
            _FakeEntryPoint("a", a),
            _FakeEntryPoint("b", b),
        ]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        await registry.stop()

    assert calls == ["b", "a"]  # reverse load order
    assert len(shutdown_events) == 1
    assert shutdown_events[0].payload == {"reason": "stop"}

    await bus.close()


def testvalidate_install_source_accepts_common_forms(tmp_path: Path) -> None:
    """PyPI names, absolute paths, and file:// / https:// URLs are accepted."""
    validate_install_source("yaya-tool-bash")
    validate_install_source("yaya-tool-bash==1.2.3")
    validate_install_source(str(tmp_path))  # absolute path
    validate_install_source("file:///tmp/my-plugin")
    validate_install_source("https://example.com/plugin.whl")


def testvalidate_install_source_rejects_hazards() -> None:
    """Shell metachars, unsupported schemes, and empty input are rejected."""
    for bad in ["", "foo; rm -rf /", "git+ssh://example.com/x.git", "http://plain"]:
        with pytest.raises(ValueError):
            validate_install_source(bad)


def test_plugin_status_values() -> None:
    """PluginStatus is a closed four-member StrEnum.

    ``unloading`` is the transient state between a threshold-breached
    ``plugin.error`` and ``on_unload`` completing — see
    ``test_concurrent_errors_trigger_single_unload``.
    """
    assert {s.value for s in PluginStatus} == {"loaded", "unloading", "failed", "unloaded"}


async def test_entry_point_load_exception_emits_plugin_error(tmp_path: Path) -> None:
    """An entry point whose ``load()`` raises surfaces a plugin.error, not a crash."""
    bus = EventBus()
    errors: list[Event] = []
    bus.subscribe("plugin.error", _collector(errors), source="observer")

    class _BoomEP:
        name = "boom"
        dist = _FakeDist("third-party-pkg")

        def load(self) -> Any:
            raise ImportError("module missing")

    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_BoomEP()]),  # type: ignore[list-item]
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    assert any("entry_point_load_failed" in str(e.payload.get("error", "")) for e in errors)
    await registry.stop()
    await bus.close()


async def test_entry_point_resolves_to_non_plugin_emits_error(tmp_path: Path) -> None:
    """Entry point yielding an object that is not a Plugin → plugin.error."""
    bus = EventBus()
    errors: list[Event] = []
    bus.subscribe("plugin.error", _collector(errors), source="observer")

    # A bare object is not a Plugin; isinstance(obj, Plugin) fails.
    not_a_plugin = object()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("nope", not_a_plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    assert any(e.payload.get("error") == "invalid_plugin_object" for e in errors)
    await registry.stop()
    await bus.close()


async def test_on_load_failure_marks_status_failed(tmp_path: Path) -> None:
    """A plugin whose ``on_load`` raises is recorded with status=failed."""
    bus = EventBus()

    class _BrokenLoader(_RecordingPlugin):
        name = "broken-loader"

        async def on_load(self, ctx: KernelContext) -> None:
            raise RuntimeError("refuse to load")

    broken = _BrokenLoader()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("x", broken)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    rows = registry.snapshot()
    assert rows[0]["status"] == "failed"
    # on_load failures should NOT have subscribed anything — no handlers leak.
    assert registry._records["broken-loader"].subs == []

    await registry.stop()
    await bus.close()


async def test_remove_calls_subprocess_and_unloads(tmp_path: Path) -> None:
    """``remove`` shells to uv pip uninstall and unloads the in-memory record."""
    bus = EventBus()
    removed: list[Event] = []
    bus.subscribe("plugin.removed", _collector(removed), source="observer")

    plugin = _RecordingPlugin()
    plugin.name = "third-x"

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    # First discovery returns the plugin; post-remove discovery returns [].
    calls = iter([[_FakeEntryPoint("x", plugin)], []])

    def _eps(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return next(calls, [])

    with (
        patch("yaya.kernel.registry.entry_points", side_effect=_eps),
        patch("yaya.kernel.registry.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        ) as mock_exec,
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        await registry.remove("third-x")

    mock_exec.assert_awaited_once()
    argv = mock_exec.call_args.args
    assert "uninstall" in argv and "third-x" in argv
    assert plugin.on_unload_calls == 1
    assert any(e.payload["name"] == "third-x" for e in removed)

    await registry.stop()
    await bus.close()


async def test_duplicate_name_is_skipped(tmp_path: Path) -> None:
    """A second entry point with a name the registry already knows is a no-op."""
    bus = EventBus()
    plugin_a = _RecordingPlugin()
    plugin_a.name = "dup"
    plugin_b = _RecordingPlugin()
    plugin_b.name = "dup"

    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([
            _FakeEntryPoint("a", plugin_a),
            _FakeEntryPoint("b", plugin_b),
        ]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    # Only the first ever had on_load invoked.
    assert plugin_a.on_load_calls == 1
    assert plugin_b.on_load_calls == 0

    await registry.stop()
    await bus.close()


async def test_install_editable_passes_dash_e(tmp_path: Path) -> None:
    """``install(path, editable=True)`` forwards ``-e`` to uv pip install."""
    bus = EventBus()
    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    with (
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        ) as mock_exec,
        patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points([])),
        patch("yaya.kernel.registry.shutil.which", return_value="/usr/bin/uv"),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        path = str(tmp_path)
        await registry.install(path, editable=True)

    argv = mock_exec.call_args.args
    assert "-e" in argv and path in argv

    await registry.stop()
    await bus.close()


async def test_unload_swallows_on_unload_exception(tmp_path: Path) -> None:
    """A plugin raising in ``on_unload`` doesn't prevent clean shutdown."""
    bus = EventBus()

    class _UnloadBoom(_RecordingPlugin):
        name = "unload-boom"

        async def on_unload(self, ctx: KernelContext) -> None:
            raise RuntimeError("cleanup failed")

    plugin = _UnloadBoom()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("x", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        # Should not raise despite the plugin's on_unload raising.
        await registry.stop()
    await bus.close()


async def test_subscriptions_raising_is_isolated(tmp_path: Path) -> None:
    """A plugin whose ``subscriptions()`` raises still loads, just without subs."""
    bus = EventBus()

    class _SubsBoom(_RecordingPlugin):
        name = "subs-boom"

        def subscriptions(self) -> list[str]:
            raise RuntimeError("list me not")

    plugin = _SubsBoom()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("x", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    rows = registry.snapshot()
    assert rows[0]["status"] == "loaded"
    assert registry._records["subs-boom"].subs == []

    await registry.stop()
    await bus.close()


def test_ep_is_bundled_none_dist_is_false(tmp_path: Path) -> None:
    """Entry points without an associated distribution are not bundled."""
    bus = EventBus()
    registry = PluginRegistry(bus, state_dir=tmp_path)

    class _OrphanEP:
        name = "orphan"
        dist = None

    assert registry._is_ep_bundled(_OrphanEP()) is False  # type: ignore[arg-type]


def test_yaya_version_falls_back_on_missing_dist() -> None:
    """``_yaya_version`` returns a sentinel when the distribution lookup fails."""
    from importlib.metadata import PackageNotFoundError

    from yaya.kernel.registry import _yaya_version

    with patch(
        "yaya.kernel.registry.distribution",
        side_effect=PackageNotFoundError("yaya"),
    ):
        assert _yaya_version() == "0.0.0"


async def test_plugin_error_payload_without_name_is_ignored(tmp_path: Path) -> None:
    """A plugin.error event missing ``name`` is logged and dropped, not crashed."""
    bus = EventBus()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    # Publish a malformed plugin.error directly — registry should not throw.
    await bus.publish(
        new_event(
            "plugin.error",
            {"error": "oops"},  # no `name` field
            session_id="kernel",
            source="kernel",
        )
    )
    await asyncio.sleep(0.01)

    await registry.stop()
    await bus.close()


# ---------------------------------------------------------------------------
# Regression tests for PR #49 review findings.
# ---------------------------------------------------------------------------


class _ToggleFailPlugin:
    """Plugin whose ``on_event`` alternates between raising and succeeding.

    Used to prove the failure counter resets on a successful
    ``on_event`` invocation (PR #49 P0).
    """

    name = "toggle-tool"
    version = "0.1.0"
    category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.fail = True  # toggle from the test
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


async def test_error_counter_resets_on_successful_event(tmp_path: Path) -> None:
    """P0 — a successful on_event resets the consecutive-error counter.

    Fires 2 failing requests, one success, then 2 more failures. With a
    threshold of 3, the plugin must remain loaded (error_count == 2
    after the second post-reset failure, not 4 cumulatively).
    """
    bus = EventBus(handler_timeout_s=1.0)
    removed: list[Event] = []
    errors: list[Event] = []
    bus.subscribe("plugin.removed", _collector(removed), source="observer")
    bus.subscribe("plugin.error", _collector(errors), source="observer")

    plugin = _ToggleFailPlugin()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("toggle", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=3)
        await registry.start()

    async def _fire() -> None:
        await bus.publish(
            new_event(
                "tool.call.request",
                {"id": "x", "name": "noop", "args": {}},
                session_id="s",
                source="kernel",
            )
        )

    # 2 failures.
    plugin.fail = True
    await _fire()
    await _fire()
    await asyncio.wait_for(_drain_until(lambda: len(errors) >= 2, bus), timeout=1.0)

    # 1 success — must reset the counter.
    plugin.fail = False
    await _fire()
    # Let the success path drain and reset fire.
    await asyncio.wait_for(
        _drain_until(lambda: registry._records["toggle-tool"].error_count == 0, bus),
        timeout=1.0,
    )

    # 2 more failures (below the threshold of 3).
    plugin.fail = True
    await _fire()
    await _fire()
    await asyncio.wait_for(_drain_until(lambda: len(errors) >= 4, bus), timeout=1.0)

    # Cumulative failures = 4, but consecutive since last success = 2 — still loaded.
    assert not removed, "plugin must not be unloaded below the threshold"
    record = registry._records["toggle-tool"]
    assert record.status is PluginStatus.LOADED
    assert record.error_count == 2

    await registry.stop()
    await bus.close()


async def test_remove_bundled_plugin_raises_even_before_load(tmp_path: Path) -> None:
    """P1 — remove() blocks a bundled entry point even if it hasn't been loaded.

    The registry instance is constructed but :meth:`start` is *not* called,
    so ``_discover_and_load`` has never run. ``remove()`` must still reject
    the bundled name by consulting entry-point metadata directly.
    """
    bus = EventBus()
    plugin = _RecordingPlugin()
    plugin.name = "bundled-web"
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("web", plugin, bundled=True)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        with pytest.raises(ValueError, match="bundled"):
            await registry.remove("web")  # ep.name, not plugin.name — still blocked.

    await bus.close()


async def test_remove_bundled_plugin_raises_when_load_failed(tmp_path: Path) -> None:
    """P1 — remove() still blocks a bundled plugin whose load() raised."""
    bus = EventBus()

    class _BoomBundledEP:
        name = "bundled-broken"
        dist = _FakeDist("yaya")

        def load(self) -> Any:
            raise ImportError("bundled import broke")

    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_BoomBundledEP()]),  # type: ignore[list-item]
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()  # discovery records the name even though load() blew up.
        with pytest.raises(ValueError, match="bundled"):
            await registry.remove("bundled-broken")

    await registry.stop()
    await bus.close()


def test_validate_accepts_windows_forward_slash_path() -> None:
    """P2 — ``C:/foo`` accepted regardless of the runner's OS."""
    validate_install_source("C:/Users/x/plugin")


def test_validate_accepts_windows_backslash_path() -> None:
    """P2 — ``C:\\foo`` accepted regardless of the runner's OS."""
    validate_install_source("C:\\Users\\x\\plugin")


async def test_zero_plugins_logs_info(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """P2 — start() with no entry points emits an INFO log."""
    bus = EventBus()
    with (
        patch("yaya.kernel.registry.entry_points", side_effect=_fake_entry_points([])),
        caplog.at_level(logging.INFO, logger="yaya.kernel.registry"),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    assert any("no plugins discovered" in rec.message for rec in caplog.records), (
        "expected an INFO log about zero plugins"
    )

    await registry.stop()
    await bus.close()


def test_ep_is_bundled_none_dist_logs_warning_once(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P2 — a None-dist entry point logs a WARNING at most once per name.

    Repeated discovery passes (install / remove each re-run
    ``_discover_and_load``) must not spam the log with the same
    "no distribution metadata" warning.
    """
    bus = EventBus()
    registry = PluginRegistry(bus, state_dir=tmp_path)

    class _OrphanEP:
        name = "orphan"
        dist = None

    with caplog.at_level(logging.WARNING, logger="yaya.kernel.registry"):
        assert registry._is_ep_bundled(_OrphanEP()) is False  # type: ignore[arg-type]
        # Subsequent passes must NOT emit another warning for the same name.
        assert registry._is_ep_bundled(_OrphanEP()) is False  # type: ignore[arg-type]
        assert registry._is_ep_bundled(_OrphanEP()) is False  # type: ignore[arg-type]

    warnings = [rec for rec in caplog.records if "no distribution metadata" in rec.message]
    assert len(warnings) == 1, f"expected exactly one WARNING across 3 discovery passes, got {len(warnings)}"


async def test_orderly_stop_reports_unloaded_not_failed_even_with_error_count(
    tmp_path: Path,
) -> None:
    """P2 — orderly stop sets status=unloaded even when error_count > 0.

    Only the threshold-driven failure path earns the ``failed`` status;
    a plugin that ticked up a few errors but never breached the
    threshold should report ``unloaded`` after an orderly ``stop()``.
    """
    bus = EventBus()
    plugin = _RecordingPlugin()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("stub", plugin)]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=5)
        await registry.start()

    registry._records["stub-tool"].error_count = 2  # below threshold
    await registry.stop()
    rows = registry.snapshot()
    assert rows[0]["status"] == "unloaded"

    await bus.close()


async def test_plugin_error_payload_without_name_logs_payload(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P3 — the 'no name' warning includes the event payload for debuggability."""
    bus = EventBus()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([]),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()

    with caplog.at_level(logging.WARNING, logger="yaya.kernel.registry"):
        await bus.publish(
            new_event(
                "plugin.error",
                {"error": "oops-no-name"},
                session_id="kernel",
                source="kernel",
            )
        )
        await asyncio.sleep(0.01)

    assert any("oops-no-name" in rec.message for rec in caplog.records), (
        "warning must include the payload so operators can diagnose the source"
    )

    await registry.stop()
    await bus.close()


def test_yaya_version_narrows_to_package_not_found() -> None:
    """P3 — _yaya_version only catches PackageNotFoundError, not bare Exception."""
    from importlib.metadata import PackageNotFoundError

    from yaya.kernel.registry import _yaya_version

    with patch(
        "yaya.kernel.registry.distribution",
        side_effect=PackageNotFoundError("yaya"),
    ):
        assert _yaya_version() == "0.0.0"

    # A non-PackageNotFoundError must propagate (guards against over-broad except).
    with (
        patch(
            "yaya.kernel.registry.distribution",
            side_effect=RuntimeError("unexpected"),
        ),
        pytest.raises(RuntimeError),
    ):
        _yaya_version()


async def test_concurrent_errors_trigger_single_unload(tmp_path: Path) -> None:
    """Round-2 P1 — concurrent plugin.error bursts must not dupe unload tasks.

    10 sessions each deliver one ``tool.call.request`` that makes the
    plugin raise. All 10 ``plugin.error`` events arrive at the registry
    handler near-simultaneously. Without the synchronous
    ``PluginStatus.UNLOADING`` flip (claimed BEFORE ``create_task``
    returns), 8 of those handler invocations would see
    ``status is LOADED`` and spawn parallel unload tasks — ``on_unload``
    would run 8 times and ``plugin.removed`` would fire 8 times.
    """
    bus = EventBus(handler_timeout_s=1.0)
    removed: list[Event] = []
    bus.subscribe("plugin.removed", _collector(removed), source="observer")

    plugin = _FailingPlugin()
    with patch(
        "yaya.kernel.registry.entry_points",
        side_effect=_fake_entry_points([_FakeEntryPoint("flaky", plugin)]),
    ):
        # Threshold 1 so the very first error on every session breaches.
        registry = PluginRegistry(bus, state_dir=tmp_path, failure_threshold=1)
        await registry.start()

    # Fan 10 sessions in parallel — each produces one plugin.error.
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
    await asyncio.wait_for(_drain_until(lambda: len(removed) >= 1, bus), timeout=1.0)

    # on_unload fires exactly once; plugin.removed emitted exactly once.
    assert plugin.on_unload_calls == 1, (
        f"expected exactly 1 on_unload call, got {plugin.on_unload_calls} "
        "— rival plugin.error events spawned duplicate unload tasks"
    )
    assert len(removed) == 1, f"expected exactly 1 plugin.removed event, got {len(removed)}"
    statuses = {row["name"]: row["status"] for row in registry.snapshot()}
    assert statuses["flaky-tool"] == "failed"

    await registry.stop()
    await bus.close()


async def test_remove_cleans_snapshot_row(tmp_path: Path) -> None:
    """Round-2 P2 — ``remove()`` prunes the record so snapshot stops listing it."""
    bus = EventBus()
    plugin = _RecordingPlugin()
    plugin.name = "removable"

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    calls = iter([[_FakeEntryPoint("x", plugin)], []])

    def _eps(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return next(calls, [])

    with (
        patch("yaya.kernel.registry.entry_points", side_effect=_eps),
        patch("yaya.kernel.registry.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "yaya.kernel.registry.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        ),
    ):
        registry = PluginRegistry(bus, state_dir=tmp_path)
        await registry.start()
        assert any(r["name"] == "removable" for r in registry.snapshot())

        await registry.remove("removable")

    # Record pruned: snapshot no longer lists the uninstalled plugin.
    assert not any(r["name"] == "removable" for r in registry.snapshot())
    assert "removable" not in registry._records
    assert "removable" not in registry._load_order
    assert "removable" not in registry._bundled_names

    await registry.stop()
    await bus.close()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _collector(bucket: list[Event]) -> Any:
    async def _handler(ev: Event) -> None:
        bucket.append(ev)

    return _handler


async def _drain_until(predicate: Any, bus: EventBus) -> None:
    """Yield control until ``predicate()`` is true.

    The bus executes session workers on the event loop; a single
    ``await asyncio.sleep(0)`` is usually enough, but the failure-unload
    path chains through two worker hops (plugin.error delivery → unload
    task → plugin.removed publish), so we loop briefly.
    """
    _ = bus
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("predicate never became true")
