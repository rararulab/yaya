"""Tests for ``yaya doctor`` — kernel smoke + per-plugin health report."""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


def test_doctor_json_ok(runner: CliRunner, cli_app) -> None:
    """Happy path: round-trip fires, every bundled plugin reports a status."""
    result = runner.invoke(cli_app, ["--json", "doctor"])
    assert result.exit_code in (0, 1), result.stdout
    payload = json.loads(result.stdout)
    assert payload["action"] == "doctor"
    assert isinstance(payload["roundtrip"], dict)
    assert isinstance(payload["plugins"], list)
    # Every bundled plugin must have produced a row.
    names = {row["name"] for row in payload["plugins"]}
    expected = {
        "llm-echo",
        "tool-bash",
        "memory-sqlite",
        "strategy-react",
    }
    assert expected.issubset(names), names


def test_doctor_text_renders_table(runner: CliRunner, cli_app) -> None:
    """Text mode prints the rich table header and round-trip line."""
    result = runner.invoke(cli_app, ["doctor"])
    # Exit may be 0 or 1 depending on bundled plugin health on the
    # runner; we only assert on rendering here.
    assert "round-trip" in result.stdout
    assert "plugin" in result.stdout.lower()


def test_doctor_help_includes_example(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "Examples" in result.stdout


def test_doctor_startup_failure(runner: CliRunner, cli_app) -> None:
    """When AgentLoop.start raises, the command surfaces ok=false, exit 1."""
    with patch("yaya.cli.commands.doctor.AgentLoop") as fake_loop_cls:
        fake_loop_cls.return_value.start.side_effect = RuntimeError("boom")
        result = runner.invoke(cli_app, ["--json", "doctor"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "kernel_startup_failed" in payload["error"]
    assert payload["suggestion"]


def test_doctor_roundtrip_timeout_exits_one(runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the bus silently drops the synthetic event, doctor exits 1."""

    async def _noop_publish(self, event):
        return None

    monkeypatch.setattr("yaya.kernel.bus.EventBus.publish", _noop_publish)
    result = runner.invoke(cli_app, ["--json", "doctor", "--timeout", "0.2"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "event_bus_unresponsive"
    assert payload["roundtrip"]["ok"] is False


def test_doctor_failed_plugin_exits_one(runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin reporting ``failed`` produces exit 1 and a json error marker."""
    from yaya.cli.commands import doctor as doctor_mod
    from yaya.kernel import HealthReport

    real_check_one = doctor_mod._check_one

    async def _force_failed(plugin, ctx, *, timeout_s):
        result = await real_check_one(plugin, ctx, timeout_s=timeout_s)
        if plugin.name == "tool-bash":
            result.status = "failed"
            result.summary = "synthetic failure"
        return result

    monkeypatch.setattr(doctor_mod, "_check_one", _force_failed)
    # Keep the HealthReport import alive for type checkers.
    _ = HealthReport

    result = runner.invoke(cli_app, ["--json", "doctor"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "plugin_failed"
    failed = [r for r in payload["plugins"] if r["status"] == "failed"]
    assert failed and failed[0]["name"] == "tool-bash"


def test_doctor_degraded_is_exit_zero(runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """``degraded`` is the install-day "not fully configured" state and must NOT fail."""
    from yaya.cli.commands import doctor as doctor_mod

    real_check_one = doctor_mod._check_one

    async def _force_degraded(plugin, ctx, *, timeout_s):
        result = await real_check_one(plugin, ctx, timeout_s=timeout_s)
        result.status = "degraded"
        result.summary = "synthetic degraded"
        return result

    monkeypatch.setattr(doctor_mod, "_check_one", _force_degraded)

    result = runner.invoke(cli_app, ["--json", "doctor"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert all(p["status"] == "degraded" for p in payload["plugins"])


def test_doctor_health_check_timeout_surfaces_degraded(tmp_path) -> None:
    """A plugin whose ``health_check`` hangs MUST NOT block the doctor run."""
    import asyncio
    import logging

    from yaya.cli.commands.doctor import _check_one
    from yaya.kernel.bus import EventBus
    from yaya.kernel.plugin import Category, KernelContext

    class _HangPlug:
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
            await asyncio.sleep(10)
            raise RuntimeError("unreachable")

    ctx = KernelContext(
        bus=EventBus(),
        logger=logging.getLogger("test"),
        config={},
        state_dir=tmp_path,
        plugin_name="hang",
    )
    result = asyncio.run(_check_one(_HangPlug(), ctx, timeout_s=0.05))
    assert result.status == "degraded"
    assert "timed out" in result.summary


def test_doctor_health_check_exception_surfaces_failed(tmp_path) -> None:
    """A plugin whose ``health_check`` raises is reported as ``failed``, doctor survives."""
    import asyncio
    import logging

    from yaya.cli.commands.doctor import _check_one
    from yaya.kernel.bus import EventBus
    from yaya.kernel.plugin import Category, KernelContext

    class _BoomPlug:
        name = "boom"
        version = "0"
        category = Category.TOOL
        requires: ClassVar[list[str]] = []

        def subscriptions(self) -> list[str]:
            return []

        async def on_load(self, ctx) -> None: ...
        async def on_event(self, ev, ctx) -> None: ...
        async def on_unload(self, ctx) -> None: ...

        async def health_check(self, ctx):
            raise RuntimeError("bad probe")

    ctx = KernelContext(
        bus=EventBus(),
        logger=logging.getLogger("test"),
        config={},
        state_dir=tmp_path,
        plugin_name="boom",
    )
    result = asyncio.run(_check_one(_BoomPlug(), ctx, timeout_s=1.0))
    assert result.status == "failed"
    assert "bad probe" in result.summary


def test_doctor_default_when_no_health_check() -> None:
    """Plugins without ``health_check`` inherit the synthesised ``ok`` default."""
    import asyncio

    from yaya.cli.commands.doctor import _check_one
    from yaya.kernel.plugin import Category

    class _BarePlug:
        name = "bare"
        version = "0"
        category = Category.TOOL
        requires: ClassVar[list[str]] = []

        def subscriptions(self) -> list[str]:
            return []

        async def on_load(self, ctx) -> None: ...
        async def on_event(self, ev, ctx) -> None: ...
        async def on_unload(self, ctx) -> None: ...

    result = asyncio.run(_check_one(_BarePlug(), None, timeout_s=1.0))
    assert result.status == "ok"
    assert "no checks registered" in result.summary


def test_doctor_timeout_flag_overrides_default(runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--timeout`` feeds through to ``_run_doctor``."""
    from yaya.cli.commands import doctor as doctor_mod

    captured: dict[str, float] = {}
    real_run = doctor_mod._run_doctor

    async def _recording(*, round_trip_timeout_s: float, health_timeout_s: float):
        captured["round_trip_timeout_s"] = round_trip_timeout_s
        captured["health_timeout_s"] = health_timeout_s
        return await real_run(
            round_trip_timeout_s=round_trip_timeout_s,
            health_timeout_s=health_timeout_s,
        )

    monkeypatch.setattr(doctor_mod, "_run_doctor", _recording)

    result = runner.invoke(cli_app, ["--json", "doctor", "--timeout", "2.5"])
    assert result.exit_code in (0, 1), result.stdout
    assert captured["health_timeout_s"] == 2.5


def test_doctor_timeout_rejects_below_min(runner: CliRunner, cli_app) -> None:
    """``--timeout`` below 0.1s is an argv error (exit 2)."""
    result = runner.invoke(cli_app, ["doctor", "--timeout", "0.0"])
    assert result.exit_code == 2


def test_doctor_json_emits_single_object_without_toast(
    runner: CliRunner, cli_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``yaya --json doctor`` must not leak an update toast into stdout/stderr."""
    from yaya.core import updater

    monkeypatch.delenv("YAYA_NO_AUTO_UPDATE", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text('{"version": "999.0.0", "checked_at": 9999999999}', encoding="utf-8")

    result = runner.invoke(cli_app, ["--json", "doctor"])
    assert result.exit_code in (0, 1)
    # stdout must parse as exactly one JSON object.
    _: dict[str, Any] = json.loads(result.stdout)
    # stderr must not contain the update-toast version string.
    assert "999.0.0" not in (result.stderr or "")
