"""Tests for ``scripts/check_coverage.py``.

These tests synthesise tiny ``coverage.xml`` fixtures rather than
running pytest-cov itself — we're testing the gate evaluator, not the
coverage tooling.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_coverage.py"


def _load_script() -> Any:
    """Import ``check_coverage.py`` as a module without installing it."""
    spec = importlib.util.spec_from_file_location("check_coverage", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load coverage checker from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_coverage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> Any:
    """Return the loaded ``check_coverage`` module."""
    return _load_script()


def _build_xml(classes: list[tuple[str, int, int]]) -> str:
    """Build a coverage.xml string with ``(filename, total, covered)`` rows.

    Each ``<class>`` gets ``total`` ``<line>`` nodes where the first
    ``covered`` have ``hits="1"`` and the rest have ``hits="0"``.
    """
    parts = [
        '<?xml version="1.0" ?>',
        "<coverage>",
        "  <sources><source>/repo/src/yaya</source></sources>",
        "  <packages><package><classes>",
    ]
    for filename, total, covered in classes:
        parts.append(f'    <class filename="{filename}"><lines>')
        for i in range(total):
            hits = 1 if i < covered else 0
            parts.append(f'      <line number="{i + 1}" hits="{hits}"/>')
        parts.append("    </lines></class>")
    parts.extend(["  </classes></package></packages>", "</coverage>"])
    return "\n".join(parts)


def _write_passing_xml(path: Path) -> None:
    """Write a coverage.xml that clears every gate."""
    rows = [
        ("kernel/bus.py", 100, 99),  # 99%
        ("kernel/loop.py", 100, 98),  # 99% combined kernel
        ("core/updater.py", 100, 95),  # 95%
        ("cli/commands/hello.py", 100, 90),  # 90%
        ("plugins/agent_tool/plugin.py", 100, 99),
        ("plugins/llm_echo/plugin.py", 100, 100),
        ("plugins/llm_openai/plugin.py", 100, 85),
        ("plugins/mcp_bridge/client.py", 100, 95),
        ("plugins/memory_sqlite/plugin.py", 100, 95),
        ("plugins/strategy_react/plugin.py", 100, 99),
        ("plugins/tool_bash/plugin.py", 100, 99),
        ("plugins/web/plugin.py", 100, 90),
    ]
    path.write_text(_build_xml(rows), encoding="utf-8")


def test_passing_xml_exits_zero(checker: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    xml = tmp_path / "coverage.xml"
    _write_passing_xml(xml)
    code = checker.run(xml)
    out = capsys.readouterr().out
    assert code == 0
    assert "Coverage gates:" in out
    assert "kernel/" in out


def test_kernel_regression_exits_one_and_names_kernel(
    checker: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Kernel at 94% (below the 93.5 gate? no, 94 clears) — pick 92% to fail.
    rows = [
        ("kernel/bus.py", 100, 92),  # 92% kernel — below 93.5 gate
        ("core/updater.py", 100, 95),
        ("cli/commands/hello.py", 100, 90),
        ("plugins/agent_tool/plugin.py", 100, 99),
        ("plugins/llm_echo/plugin.py", 100, 100),
        ("plugins/llm_openai/plugin.py", 100, 85),
        ("plugins/mcp_bridge/client.py", 100, 95),
        ("plugins/memory_sqlite/plugin.py", 100, 95),
        ("plugins/strategy_react/plugin.py", 100, 99),
        ("plugins/tool_bash/plugin.py", 100, 99),
        ("plugins/web/plugin.py", 100, 90),
    ]
    xml = tmp_path / "coverage.xml"
    xml.write_text(_build_xml(rows), encoding="utf-8")
    code = checker.run(xml)
    captured = capsys.readouterr()
    assert code == 1
    # Failure message goes to stderr and names the kernel prefix + threshold.
    assert "kernel/" in captured.err
    assert "93.5" in captured.err


def test_plugin_regression_names_plugin(checker: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Drop web plugin to 80% (below its 84 gate); keep everything else passing.
    rows = [
        ("kernel/bus.py", 100, 99),
        ("core/updater.py", 100, 95),
        ("cli/commands/hello.py", 100, 90),
        ("plugins/agent_tool/plugin.py", 100, 99),
        ("plugins/llm_echo/plugin.py", 100, 100),
        ("plugins/llm_openai/plugin.py", 100, 85),
        ("plugins/mcp_bridge/client.py", 100, 95),
        ("plugins/memory_sqlite/plugin.py", 100, 95),
        ("plugins/strategy_react/plugin.py", 100, 99),
        ("plugins/tool_bash/plugin.py", 100, 99),
        ("plugins/web/plugin.py", 100, 80),  # below 84 gate
    ]
    xml = tmp_path / "coverage.xml"
    xml.write_text(_build_xml(rows), encoding="utf-8")
    code = checker.run(xml)
    err = capsys.readouterr().err
    assert code == 1
    assert "plugins/web/" in err


def test_missing_xml_returns_two(checker: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.xml"
    code = checker.run(missing)
    err = capsys.readouterr().err
    assert code == 2
    assert "not found" in err


def test_aggregate_empty_prefix_reports_100(checker: Any) -> None:
    # Empty groups (no matching files) must not cause a false failure.
    result = checker.aggregate({}, "ghost/")
    assert result.total == 0
    assert result.percent == 100.0


def test_main_parses_xml_argument(checker: Any, tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    _write_passing_xml(xml)
    code = checker.main(["--xml", str(xml)])
    assert code == 0


def test_summary_suggests_ratchet_for_improved_modules(checker: Any, tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    _write_passing_xml(xml)
    files = checker.parse_coverage(xml)
    global_res = checker.GateResult(coverage=checker.global_coverage(files), threshold=checker.GLOBAL_GATE)
    module_res = checker.evaluate_gates(files, checker.MODULE_GATES)
    plugin_res = checker.evaluate_gates(files, checker.PLUGIN_GATES)
    text = checker.format_summary(global_res, module_res, plugin_res)
    # Modules with plenty of headroom must surface a ratchet suggestion.
    assert "ratchet->" in text
    assert "94.5" in text  # global at 95.33% -> ratchet suggestion 94.5

    # A gate exactly at reality should get the ASCII placeholder.
    tight = checker.GateResult(
        coverage=checker.ModuleCoverage(prefix="tight/", covered=90, total=100),
        threshold=90.0,
    )
    text2 = checker.format_summary(global_res, [tight], [])
    assert " -- " in text2 or text2.rstrip().endswith("--")
