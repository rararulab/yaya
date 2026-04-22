"""Focused tests for every bundled plugin's ``health_check``.

Consolidated here (rather than one test file per plugin) because each
check is a 5-line assertion and scattering them across the
per-plugin test packages would dwarf the actual plugin coverage. The
companion spec is ``specs/kernel-health.spec``.

Contract per plugin:
    * Returns a :class:`~yaya.kernel.HealthReport`.
    * Fast (<500 ms) — every test below runs sync-ish with no I/O.
    * No real LLM / network call — ``openai`` import is mocked where
      unavoidable (e.g. :class:`OpenAIProvider`) even though the
      check never reaches the SDK.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.plugin import HealthReport, KernelContext


def _ctx(tmp_path: Path, name: str) -> KernelContext:
    """Build a bare :class:`KernelContext` for a stateless health_check.

    Each plugin's health_check is self-contained — the context is only
    used to satisfy the ABI signature.
    """
    return KernelContext(
        bus=EventBus(),
        logger=logging.getLogger(f"plugin.{name}"),
        config={},
        state_dir=tmp_path,
        plugin_name=name,
    )


def test_llm_echo_ok(tmp_path: Path) -> None:
    from yaya.plugins.llm_echo.plugin import EchoLLM

    plug = EchoLLM()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "llm-echo")))
    assert isinstance(report, HealthReport)
    assert report.status == "ok"
    assert "echo provider ready" in report.summary


def test_llm_openai_degraded_without_api_key(tmp_path: Path) -> None:
    from yaya.plugins.llm_openai.plugin import OpenAIProvider

    plug = OpenAIProvider()
    # No clients populated → degraded
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "llm-openai")))
    assert report.status == "degraded"
    assert "api_key" in report.summary


def test_llm_openai_ok_when_clients_populated(tmp_path: Path) -> None:
    from yaya.plugins.llm_openai.plugin import OpenAIProvider

    plug = OpenAIProvider()
    plug._clients["stub"] = object()  # type: ignore[assignment]
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "llm-openai")))
    assert report.status == "ok"
    assert "1 instance ready" in report.summary


def test_tool_bash_ok_when_bash_on_path(tmp_path: Path) -> None:
    from yaya.plugins.tool_bash.plugin import BashTool

    plug = BashTool()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "tool-bash")))
    # Every dev/CI machine has bash; skip if somehow absent.
    import shutil

    if shutil.which("bash") is None:
        pytest.skip("bash not installed on this runner")
    assert report.status == "ok"
    assert "bash at" in report.summary


def test_tool_bash_failed_when_bash_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from yaya.plugins.tool_bash.plugin import BashTool

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    plug = BashTool()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "tool-bash")))
    assert report.status == "failed"
    assert "not found on PATH" in report.summary


def test_mcp_bridge_ok_when_no_servers(tmp_path: Path) -> None:
    from yaya.plugins.mcp_bridge.plugin import MCPBridge

    plug = MCPBridge()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "mcp-bridge")))
    assert report.status == "ok"
    assert "no servers configured" in report.summary


def test_memory_sqlite_failed_when_not_loaded(tmp_path: Path) -> None:
    from yaya.plugins.memory_sqlite.plugin import SqliteMemory

    plug = SqliteMemory()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "memory-sqlite")))
    assert report.status == "failed"
    assert "not opened" in report.summary


def test_memory_sqlite_failed_when_probe_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken DB (probe raises) surfaces ``failed`` without killing doctor."""
    import sys

    from yaya.plugins.memory_sqlite.plugin import SqliteMemory

    async def _go() -> HealthReport:
        plug = SqliteMemory()
        ctx = _ctx(tmp_path, "memory-sqlite")
        await plug.on_load(ctx)
        try:
            mod = sys.modules["yaya.plugins.memory_sqlite.plugin"]

            def _raise(_conn):
                raise RuntimeError("disk gone")

            monkeypatch.setattr(mod, "_probe_select_one", _raise)
            return await plug.health_check(ctx)
        finally:
            await plug.on_unload(ctx)

    report = asyncio.run(_go())
    assert report.status == "failed"
    assert "disk gone" in report.summary


def test_memory_sqlite_failed_on_unexpected_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that returns anything other than ``(1,)`` is surfaced as failed."""
    import sys

    from yaya.plugins.memory_sqlite.plugin import SqliteMemory

    async def _go() -> HealthReport:
        plug = SqliteMemory()
        ctx = _ctx(tmp_path, "memory-sqlite")
        await plug.on_load(ctx)
        try:
            mod = sys.modules["yaya.plugins.memory_sqlite.plugin"]
            monkeypatch.setattr(mod, "_probe_select_one", lambda _conn: (2,))
            return await plug.health_check(ctx)
        finally:
            await plug.on_unload(ctx)

    report = asyncio.run(_go())
    assert report.status == "failed"
    assert "unexpected row" in report.summary


def test_memory_sqlite_ok_after_on_load(tmp_path: Path) -> None:
    from yaya.plugins.memory_sqlite.plugin import SqliteMemory

    async def _go() -> HealthReport:
        plug = SqliteMemory()
        ctx = _ctx(tmp_path, "memory-sqlite")
        await plug.on_load(ctx)
        try:
            return await plug.health_check(ctx)
        finally:
            await plug.on_unload(ctx)

    report = asyncio.run(_go())
    assert report.status == "ok"
    assert "db at" in report.summary


def test_strategy_react_degraded_without_providers(tmp_path: Path) -> None:
    from yaya.plugins.strategy_react.plugin import ReActStrategy

    plug = ReActStrategy()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "strategy-react")))
    assert report.status == "degraded"
    assert "fallback" in report.summary


def test_web_ok_when_bundle_present(tmp_path: Path) -> None:
    from yaya.plugins.web.plugin import WebAdapter

    plug = WebAdapter()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "web")))
    assert report.status == "ok"
    assert "bundle" in report.summary


def test_agent_tool_degraded_without_runtime(tmp_path: Path) -> None:
    from yaya.plugins.agent_tool.plugin import AgentPlugin, _Runtime

    # Ensure bindings are clear for this test.
    _Runtime.session = None
    _Runtime.bus = None
    _Runtime.plugin_ctx = None
    plug = AgentPlugin()
    report = asyncio.run(plug.health_check(_ctx(tmp_path, "agent-tool")))
    assert report.status == "degraded"
    assert "runtime bindings" in report.summary
