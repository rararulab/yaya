"""Pytest-bdd execution of ``specs/kernel-health.spec`` scenarios.

The Gherkin text in ``features/kernel-health.feature`` is the
authoritative BDD contract for the ``yaya doctor`` command and the
optional plugin ``health_check`` ABI. Each scenario binds to step
definitions here via pytest-bdd; drift between the two trips
``check_feature_sync`` or pytest's
``StepDefinitionNotFoundError``.

Engineering-level coverage lives in ``tests/cli/test_doctor.py`` and
``tests/plugins/test_health_checks.py``; this module keeps the
human-readable contract executable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from typer.testing import CliRunner

pytestmark = pytest.mark.unit

FEATURE_FILE = Path(__file__).parent / "features" / "kernel-health.feature"
scenarios(str(FEATURE_FILE))


@dataclass
class HealthCtx:
    """Per-scenario state for the health feature."""

    runner: CliRunner = field(default_factory=CliRunner)
    result: Any = None
    payload: dict[str, Any] | None = None
    timeout_report: Any = None


@pytest.fixture
def hctx() -> HealthCtx:
    return HealthCtx()


# -- AC-01 ------------------------------------------------------------------


@given("a fresh kernel with every bundled plugin loaded")
def _fresh_kernel(hctx: HealthCtx) -> None:
    # No action — the CLI invocation below instantiates one per call.
    _ = hctx


@when("the doctor command runs in json mode")
def _run_doctor_json(hctx: HealthCtx) -> None:
    from yaya.cli import app

    hctx.result = hctx.runner.invoke(app, ["--json", "doctor"])
    hctx.payload = json.loads(hctx.result.stdout)


@then("each bundled plugin appears in the plugins list with a status field")
def _each_plugin_has_status(hctx: HealthCtx) -> None:
    assert hctx.payload is not None
    plugins = hctx.payload["plugins"]
    assert plugins, "expected at least one bundled plugin to report"
    for row in plugins:
        assert row["status"] in ("ok", "degraded", "failed"), row


# -- AC-02 ------------------------------------------------------------------


@given("every plugin reports degraded")
def _patch_all_degraded(hctx: HealthCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    from yaya.cli.commands import doctor as doctor_mod

    real = doctor_mod._check_one

    async def _forced(plugin, ctx, *, timeout_s):
        result = await real(plugin, ctx, timeout_s=timeout_s)
        result.status = "degraded"
        result.summary = "forced degraded"
        return result

    monkeypatch.setattr(doctor_mod, "_check_one", _forced)


@when("the doctor command exits")
def _invoke_doctor(hctx: HealthCtx) -> None:
    from yaya.cli import app

    hctx.result = hctx.runner.invoke(app, ["--json", "doctor"])
    try:
        hctx.payload = json.loads(hctx.result.stdout)
    except json.JSONDecodeError:
        hctx.payload = None


@then("the exit code is zero")
def _exit_zero(hctx: HealthCtx) -> None:
    assert hctx.result.exit_code == 0, hctx.result.stdout


@then("the json ok field is true")
def _ok_true(hctx: HealthCtx) -> None:
    assert hctx.payload is not None
    assert hctx.payload["ok"] is True


# -- AC-03 ------------------------------------------------------------------


@given("one bundled plugin reports failed")
def _patch_one_failed(hctx: HealthCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    from yaya.cli.commands import doctor as doctor_mod

    real = doctor_mod._check_one

    async def _forced(plugin, ctx, *, timeout_s):
        result = await real(plugin, ctx, timeout_s=timeout_s)
        if plugin.name == "llm-echo":
            result.status = "failed"
            result.summary = "forced failure"
        return result

    monkeypatch.setattr(doctor_mod, "_check_one", _forced)


@then("the exit code is one")
def _exit_one(hctx: HealthCtx) -> None:
    assert hctx.result.exit_code == 1, hctx.result.stdout


@then("the json error field is plugin_failed")
def _error_plugin_failed(hctx: HealthCtx) -> None:
    assert hctx.payload is not None
    assert hctx.payload["error"] == "plugin_failed"


# -- AC-04 ------------------------------------------------------------------


@given("a plugin whose health check never returns")
def _hang_plug(hctx: HealthCtx) -> None:
    from yaya.kernel.plugin import Category

    class _Hang:
        name = "hang"
        version = "0"
        category = Category.TOOL
        requires: ClassVar[list[str]] = []

        def subscriptions(self) -> list[str]:
            return []

        async def on_load(self, ctx) -> None: ...
        async def on_event(self, ev, ctx) -> None: ...
        async def on_unload(self, ctx) -> None: ...

        async def health_check(self, ctx):
            await asyncio.sleep(5)

    hctx.timeout_report = _Hang()


@when("the doctor helper invokes it with a tight timeout")
def _invoke_with_tight_timeout(hctx: HealthCtx, tmp_path: Path) -> None:
    import logging

    from yaya.cli.commands.doctor import _check_one
    from yaya.kernel.bus import EventBus
    from yaya.kernel.plugin import KernelContext

    ctx = KernelContext(
        bus=EventBus(),
        logger=logging.getLogger("test"),
        config={},
        state_dir=tmp_path,
        plugin_name="hang",
    )
    hctx.timeout_report = asyncio.run(_check_one(hctx.timeout_report, ctx, timeout_s=0.05))


@then("the reported status is degraded with a timed out summary")
def _status_is_timeout_degraded(hctx: HealthCtx) -> None:
    assert hctx.timeout_report.status == "degraded"
    assert "timed out" in hctx.timeout_report.summary


# -- AC-05 ------------------------------------------------------------------


@given("a plugin without a health check method")
def _bare_plug(hctx: HealthCtx) -> None:
    from yaya.kernel.plugin import Category

    class _Bare:
        name = "bare"
        version = "0"
        category = Category.TOOL
        requires: ClassVar[list[str]] = []

        def subscriptions(self) -> list[str]:
            return []

        async def on_load(self, ctx) -> None: ...
        async def on_event(self, ev, ctx) -> None: ...
        async def on_unload(self, ctx) -> None: ...

    hctx.timeout_report = _Bare()


@when("the doctor helper invokes the default synthesiser")
def _invoke_default(hctx: HealthCtx) -> None:
    from yaya.cli.commands.doctor import _check_one

    hctx.timeout_report = asyncio.run(_check_one(hctx.timeout_report, None, timeout_s=1.0))


@then("the reported status is ok with the default summary")
def _default_ok(hctx: HealthCtx) -> None:
    assert hctx.timeout_report.status == "ok"
    assert "no checks registered" in hctx.timeout_report.summary


# -- AC-06 ------------------------------------------------------------------


@given("the event bus drops the synthetic event")
def _drop_bus_publish(hctx: HealthCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self, event):
        return None

    monkeypatch.setattr("yaya.kernel.bus.EventBus.publish", _noop)


@then("the json error field is event_bus_unresponsive")
def _error_bus_unresponsive(hctx: HealthCtx) -> None:
    # Re-invoke with a tight timeout so the scenario finishes fast. The
    # default `_invoke_doctor` step already ran; we override its output.
    from yaya.cli import app

    hctx.result = hctx.runner.invoke(app, ["--json", "doctor", "--timeout", "0.2"])
    hctx.payload = json.loads(hctx.result.stdout)
    assert hctx.payload["error"] == "event_bus_unresponsive"


_ = parsers  # reserved for future parametric steps.
