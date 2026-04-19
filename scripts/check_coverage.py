#!/usr/bin/env python3
"""Per-module coverage gate.

Runs after ``pytest --cov-report=xml`` and parses ``coverage.xml`` to
enforce **per-module** coverage thresholds in addition to the global
``fail_under`` in ``pyproject.toml``. Coverage regressions in the
kernel (which every plugin depends on) must not be hidden by high
overall coverage in plugin code.

When it runs
    * ``just test`` (invoked after pytest via the justfile recipe) so
      local runs fail fast on regression.
    * CI ``tests`` job on every PR / push, across all three OS matrix
      entries — platform-specific dips surface at PR time.

Exit codes
    0  every module meets or exceeds its gate.
    1  one or more modules are below their gate. The failing modules
       and their coverage are listed in stderr.
    2  usage error (missing ``coverage.xml``).

Ratchet policy (documented in ``docs/dev/testing.md``):
    * After any merge to ``main`` that raises a module's coverage
      above ``gate + 0.5``, raise its gate to ``actual - 0.5`` in the
      same PR or an immediate follow-up. Rounding is ``floor`` to the
      nearest 0.5.
    * Gates never retreat without a ``governance`` issue.

Only the Python standard library may be imported — this script runs
before ``uv sync`` in some CI contexts and must have no third-party
deps. See ``scripts/AGENT.md``.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Gate table
# ---------------------------------------------------------------------------
# Each gate is ``(prefix, minimum_percent)`` where ``prefix`` is matched
# against the ``filename`` attribute of ``<class>`` nodes in
# ``coverage.xml`` (paths are relative to ``src/yaya``). A gate that is
# LOWER than the current reported coverage on ``main`` is not allowed —
# see the ratchet policy above. If a gate looks low, it is because the
# module is currently below its aspirational target; raising the gate
# requires raising the coverage first.

GLOBAL_GATE = 90.0
"""Global coverage floor (also enforced by ``fail_under`` in pyproject)."""

MODULE_GATES: tuple[tuple[str, float], ...] = (
    # Kernel is the contract: bugs here blow up every plugin. Aspirational
    # gate is 95% (issue #43); current reality on main is ~94.46%, so the
    # gate sits at floor(94.46 - 0.5) = 93.5 until coverage rises.
    ("kernel/", 93.5),
    ("core/", 90.0),
    # CLI branches include rich-text / tty paths that resist coverage.
    ("cli/", 85.0),
)
"""Per-area gates for non-plugin source trees."""

PLUGIN_GATES: tuple[tuple[str, float], ...] = (
    # Each bundled plugin is gated independently — aggregating plugins
    # hides a 60% plugin behind a 95% neighbor. Gates reflect current
    # reality minus 0.5 where the 85% target is not yet met (ratchet up
    # when coverage rises). Aspirational floor per bundled plugin: 85%.
    ("plugins/agent_tool/", 95.0),
    ("plugins/llm_echo/", 95.0),
    # llm_openai exercises many provider error paths that the suite
    # does not yet hit. Gate tracks reality (~80.16%) - 0.5.
    ("plugins/llm_openai/", 79.5),
    ("plugins/mcp_bridge/", 90.0),
    ("plugins/memory_sqlite/", 89.5),
    ("plugins/strategy_react/", 95.0),
    ("plugins/tool_bash/", 95.0),
    # Web adapter has WS lifecycle branches that are hard to cover in
    # unit tests. Gate tracks reality (~84.86%) - 0.5 until a dedicated
    # WS-state test pass lands.
    ("plugins/web/", 84.0),
)
"""Per-bundled-plugin gates."""


@dataclass(frozen=True)
class ModuleCoverage:
    """Aggregated coverage for a prefix group.

    Attributes:
        prefix: Path prefix that identified the group.
        covered: Number of executed lines across the group.
        total: Number of executable lines across the group.
    """

    prefix: str
    covered: int
    total: int

    @property
    def percent(self) -> float:
        """Return line coverage as a percentage, 100.0 for empty groups."""
        if self.total == 0:
            return 100.0
        return self.covered / self.total * 100.0


@dataclass(frozen=True)
class GateResult:
    """Outcome of checking a single gate.

    Attributes:
        coverage: The coverage roll-up for the gate's prefix.
        threshold: The required minimum percent.
    """

    coverage: ModuleCoverage
    threshold: float

    @property
    def passed(self) -> bool:
        """True when coverage meets or exceeds the threshold."""
        return self.coverage.percent >= self.threshold


def parse_coverage(path: Path) -> dict[str, tuple[int, int]]:
    """Return per-file ``(covered, total)`` lines from a coverage XML file.

    Args:
        path: Path to the ``coverage.xml`` produced by ``pytest-cov``.

    Returns:
        Mapping of file path (as reported in the XML, relative to the
        ``<source>`` root) to a ``(covered, total)`` line tuple.
    """
    # ``xml.etree`` parses untrusted input; ``coverage.xml`` is produced
    # by our own test run so defusedxml is not required here.
    tree = ET.parse(path)  # noqa: S314
    root = tree.getroot()
    out: dict[str, tuple[int, int]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        if not filename:
            continue
        lines_node = cls.find("lines")
        if lines_node is None:
            continue
        total = 0
        covered = 0
        for line in lines_node.findall("line"):
            total += 1
            if int(line.get("hits", "0")) > 0:
                covered += 1
        out[filename] = (covered, total)
    return out


def aggregate(files: dict[str, tuple[int, int]], prefix: str) -> ModuleCoverage:
    """Roll up coverage for all files under ``prefix``.

    Args:
        files: Per-file coverage as returned by :func:`parse_coverage`.
        prefix: Path prefix (e.g. ``kernel/``) to match against file keys.

    Returns:
        A :class:`ModuleCoverage` summarising lines covered within the
        prefix. An empty group reports 100% to avoid false positives
        when a module has no source files (e.g. during refactors).
    """
    covered = 0
    total = 0
    for path, (cv, tv) in files.items():
        if path.startswith(prefix):
            covered += cv
            total += tv
    return ModuleCoverage(prefix=prefix, covered=covered, total=total)


def evaluate_gates(
    files: dict[str, tuple[int, int]],
    gates: Iterable[tuple[str, float]],
) -> list[GateResult]:
    """Compute :class:`GateResult` objects for each ``(prefix, threshold)``.

    Args:
        files: Per-file coverage as returned by :func:`parse_coverage`.
        gates: Iterable of ``(prefix, threshold)`` gate definitions.

    Returns:
        One :class:`GateResult` per gate, in the input order.
    """
    return [GateResult(coverage=aggregate(files, prefix), threshold=threshold) for prefix, threshold in gates]


def global_coverage(files: dict[str, tuple[int, int]]) -> ModuleCoverage:
    """Return the global line-coverage roll-up across every tracked file.

    Args:
        files: Per-file coverage as returned by :func:`parse_coverage`.

    Returns:
        A :class:`ModuleCoverage` whose ``prefix`` is ``"<global>"``.
    """
    covered = sum(cv for cv, _ in files.values())
    total = sum(tv for _, tv in files.values())
    return ModuleCoverage(prefix="<global>", covered=covered, total=total)


def format_summary(
    global_result: GateResult,
    module_results: list[GateResult],
    plugin_results: list[GateResult],
) -> str:
    """Format a ratchet-friendly summary table.

    Args:
        global_result: The global gate outcome.
        module_results: Per-area gate outcomes.
        plugin_results: Per-bundled-plugin gate outcomes.

    Returns:
        A multi-line string suitable for stdout, with one row per gate.
        The ``ratchet→`` column names the gate value a future PR could
        raise the threshold to, so maintainers can see regressions and
        ratchet opportunities at a glance.
    """
    lines = ["Coverage gates:", ""]
    header = f"  {'prefix':<30} {'covered/total':>14} {'actual':>8} {'gate':>7} {'status':>8} {'ratchet→':>10}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    def row(result: GateResult) -> str:
        cov = result.coverage
        status = "ok" if result.passed else "FAIL"
        # Suggested ratchet: actual - 0.5, floored to 0.5 grid. Only
        # emit a ratchet suggestion when it would raise the gate.
        grid = (int(cov.percent * 2) - 1) / 2  # floor(actual) - 0.5
        suggestion = f"{grid:.1f}" if grid > result.threshold else "—"
        return (
            f"  {result.coverage.prefix:<30} "
            f"{cov.covered:>6}/{cov.total:<7} "
            f"{cov.percent:>7.2f}% "
            f"{result.threshold:>6.1f}% "
            f"{status:>8} "
            f"{suggestion:>10}"
        )

    lines.append(row(global_result))
    lines.append("")
    for result in module_results:
        lines.append(row(result))
    lines.append("")
    for result in plugin_results:
        lines.append(row(result))
    return "\n".join(lines)


def format_failures(failures: list[GateResult]) -> str:
    """Return a human-readable error listing every failing gate.

    Args:
        failures: Gate results whose ``.passed`` is False.

    Returns:
        A multi-line error message naming each failing prefix, its
        measured coverage, and the required threshold.
    """
    lines = ["Coverage gate failures:"]
    for f in failures:
        cov = f.coverage
        lines.append(
            f"  {cov.prefix}: {cov.percent:.2f}% < {f.threshold:.1f}% ({cov.covered}/{cov.total} lines covered)"
        )
    lines.append("")
    lines.append("See docs/dev/testing.md for the ratchet policy.")
    return "\n".join(lines)


def run(xml_path: Path) -> int:
    """Evaluate all gates against ``xml_path`` and return a shell exit code.

    Args:
        xml_path: Path to the ``coverage.xml`` file to parse.

    Returns:
        Exit code: 0 on success, 1 on any gate failure, 2 on usage error.
    """
    if not xml_path.exists():
        print(f"error: coverage file not found: {xml_path}", file=sys.stderr)
        print("Hint: run `uv run pytest --cov --cov-report=xml` first.", file=sys.stderr)
        return 2

    files = parse_coverage(xml_path)
    global_res = GateResult(coverage=global_coverage(files), threshold=GLOBAL_GATE)
    module_res = evaluate_gates(files, MODULE_GATES)
    plugin_res = evaluate_gates(files, PLUGIN_GATES)

    print(format_summary(global_res, module_res, plugin_res))

    failures = [r for r in [global_res, *module_res, *plugin_res] if not r.passed]
    if failures:
        print("", file=sys.stderr)
        print(format_failures(failures), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector for testing; defaults to ``sys.argv``.

    Returns:
        Shell exit code from :func:`run`.
    """
    parser = argparse.ArgumentParser(description="Enforce per-module coverage gates on coverage.xml.")
    parser.add_argument(
        "--xml",
        type=Path,
        default=Path("coverage.xml"),
        help="Path to coverage.xml (default: ./coverage.xml).",
    )
    args = parser.parse_args(argv)
    return run(args.xml)


if __name__ == "__main__":
    raise SystemExit(main())
