"""Mechanical enforcement of AGENT.md §4 — no third-party agent frameworks.

What this does
--------------
Scans three surfaces and exits non-zero if it finds any hit:

1. Declared dependencies in ``pyproject.toml`` — ``[project] dependencies``,
   every ``[dependency-groups]`` table, and ``[project.optional-dependencies]``.
2. Source ``import`` statements under ``src/`` and ``tests/`` (AST-based,
   not regex — won't false-positive on strings or comments).
3. Low-level HTTP clients (``httpx`` / ``requests`` / ``aiohttp``) inside
   any ``src/yaya/plugins/llm_*/`` subpackage. LLM-provider plugins MUST
   go through the official ``openai`` or ``anthropic`` SDK — raw HTTP
   to an LLM endpoint is rejected at review. The SDKs themselves depend
   on ``httpx`` internally; that is fine because the plugin code does
   not directly import it. See ``docs/dev/plugin-protocol.md``
   "LLM providers (v1 contract)" and issue #26.

Why this exists
---------------
AGENT.md §4 bans LangChain / LangGraph / LlamaIndex / AutoGen / CrewAI /
DSPy / etc. Until this script existed, that rule was honor-system. Now
it is grep-enforced via pre-commit + CI.

When it runs
------------
- Pre-commit hook: every commit that touches ``pyproject.toml`` or any
  ``src/**.py`` / ``tests/**.py`` file.
- CI: the ``check`` job in ``.github/workflows/main.yml``.
- Locally: ``uv run python scripts/check_banned_frameworks.py``.

Exit codes
----------
- 0 — pass, no banned packages or imports found.
- 1 — fail, at least one violation reported.
- 2 — script / config error (e.g. cannot parse ``pyproject.toml``).

Known limitations
-----------------
- Dynamic imports via ``importlib.import_module("langchain")`` are NOT
  caught — AST cannot see strings. The ban is policy enforcement, not a
  hermetic sandbox. Add a runtime check if a known offender shows up.
- Transitive dependencies (banned package pulled in by a permitted
  package) are NOT scanned here — see issue #33 for the future
  ``uv.lock`` integration once the script's blast radius is well
  understood.

stdlib-only.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Source of truth: AGENT.md §4. Any drift here MUST be matched in
# AGENT.md and docs/dev/no-agent-frameworks.md in the same PR.
BANNED_PACKAGES: frozenset[str] = frozenset({
    # LangChain family.
    "langchain",
    "langchain-core",
    "langchain-community",
    "langchain-openai",
    "langchain-anthropic",
    "langgraph",
    "langsmith",
    "langserve",
    # Index / RAG frameworks.
    "llamaindex",
    "llama-index",
    "llama-index-core",
    "llama-parse",
    "haystack",
    "farm-haystack",
    "haystack-ai",
    # Multi-agent frameworks.
    "autogen",
    "pyautogen",
    "autogenstudio",
    "ag2",
    "crewai",
    "crewai-tools",
    "semantic-kernel",
    # Prompting / structured-output libraries marketed as agent frameworks.
    "instructor",
    "guidance",
    "dspy",
    "dspy-ai",
    "mirascope",
    "marvin",
    # Agent-specific toolkits.
    "griptape",
    "smol-developer",
    "smolagents",
    # Vendor agent SDKs (distinct from the plain LLM SDKs we DO permit).
    "openai-agents",
    "anthropic-agents",
})

# Informational — surfaced in script output so the rule is unambiguous.
PERMITTED_LLM_SDKS: frozenset[str] = frozenset({"openai", "anthropic"})

# Top-level import names that map to a banned distribution. Distribution
# names use hyphens, but Python imports use the actual module name. Most
# banned packages expose a module name that matches the distribution
# minus hyphens / dashes. We list explicit aliases here so the AST scan
# catches the import even when the on-disk name differs.
BANNED_IMPORT_ROOTS: frozenset[str] = frozenset({
    "langchain",
    "langchain_core",
    "langchain_community",
    "langchain_openai",
    "langchain_anthropic",
    "langgraph",
    "langsmith",
    "langserve",
    "llamaindex",
    "llama_index",
    "llama_parse",
    "haystack",
    "autogen",
    "pyautogen",
    "autogenstudio",
    "ag2",
    "crewai",
    "crewai_tools",
    "semantic_kernel",
    "instructor",
    "guidance",
    "dspy",
    "mirascope",
    "marvin",
    "griptape",
    "smol_developer",
    "smolagents",
    "openai_agents",
    "anthropic_agents",
})

# Reference text shown on failure so authors know where to look.
DOC_REFERENCE = "See docs/dev/no-agent-frameworks.md and AGENT.md §4."

# LLM-plugin HTTP ban — v1 llm-provider contract (issue #26). These
# top-level import names are rejected inside any
# ``src/yaya/plugins/llm_*/`` subpackage. The ``openai`` and
# ``anthropic`` SDKs use ``httpx`` internally; that is fine because
# the plugin does not directly import it.
LLM_PLUGIN_BANNED_IMPORT_ROOTS: frozenset[str] = frozenset({
    "httpx",
    "requests",
    "aiohttp",
})

# Documentation pointer for the v1 llm-provider contract ban.
LLM_DOC_REFERENCE = (
    "LLM-provider plugins MUST use the openai or anthropic SDK; raw HTTP "
    "clients are banned. See docs/dev/plugin-protocol.md "
    '"LLM providers (v1 contract)" and issue #26.'
)


def normalize_distribution_name(name: str) -> str:
    """Apply PyPA name normalization — lowercase, hyphenate underscores.

    See: https://packaging.python.org/en/latest/specifications/name-normalization/
    """
    return name.lower().replace("_", "-").replace(".", "-").strip()


def _strip_requirement_extras(spec: str) -> str:
    """Extract the bare distribution name from a PEP 508 requirement string.

    Handles ``foo``, ``foo>=1.0``, ``foo[bar]``, ``foo[bar,baz]==1``,
    ``foo ; python_version < '3.12'``, and the ``--editable`` / ``-e`` /
    URL forms commonly tolerated by uv. Returns ``""`` for things that
    are clearly not a distribution name (URLs, paths) — those cannot be
    matched against the ban list anyway.
    """
    # Strip environment markers.
    core = spec.split(";", 1)[0].strip()
    # Strip extras.
    core = core.split("[", 1)[0].strip()
    # Strip version specifiers.
    for sep in ("==", ">=", "<=", "!=", "~=", ">", "<", "@"):
        if sep in core:
            core = core.split(sep, 1)[0].strip()
            break
    # Bail on anything that looks like a URL or path — uv accepts those
    # but we cannot evaluate them against a name-based ban list.
    if "/" in core or core.startswith(("git+", "http://", "https://", ".")):
        return ""
    return core


@dataclass
class Violation:
    """One banned-package or banned-import hit."""

    surface: str  # "pyproject" | "import"
    package: str  # normalized distribution name OR import root
    location: str  # "pyproject.toml: [project.dependencies]" or "src/foo.py:3"
    detail: str = ""

    def render(self) -> str:
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{self.location}: {self.package}{suffix}"


@dataclass
class ScanResult:
    """Aggregate result of a full scan."""

    ok: bool = True
    violations: list[Violation] = field(default_factory=list)
    permitted_llm_sdks: list[str] = field(default_factory=lambda: sorted(PERMITTED_LLM_SDKS))
    banned_packages: list[str] = field(default_factory=lambda: sorted(BANNED_PACKAGES))


def scan_pyproject(pyproject_path: Path) -> list[Violation]:
    """Return every banned distribution declared in ``pyproject_path``.

    Scans ``[project] dependencies``, every group in
    ``[dependency-groups]``, and every extra in
    ``[project.optional-dependencies]``.
    """
    if not pyproject_path.is_file():
        return []

    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)

    violations: list[Violation] = []

    project = data.get("project", {})
    if isinstance(project, dict):
        deps = project.get("dependencies", [])
        if isinstance(deps, list):
            violations.extend(_scan_requirement_list(deps, "[project.dependencies]"))

        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for extra, items in optional.items():
                if isinstance(items, list):
                    location = f"[project.optional-dependencies.{extra}]"
                    violations.extend(_scan_requirement_list(items, location))

    groups = data.get("dependency-groups", {})
    if isinstance(groups, dict):
        for group, items in groups.items():
            if isinstance(items, list):
                location = f"[dependency-groups.{group}]"
                violations.extend(_scan_requirement_list(items, location))

    return violations


def _scan_requirement_list(items: list[Any], location: str) -> list[Violation]:
    """Filter a PEP 508 requirement list down to banned hits."""
    out: list[Violation] = []
    for item in items:
        if not isinstance(item, str):
            continue
        raw = _strip_requirement_extras(item)
        if not raw:
            continue
        normalized = normalize_distribution_name(raw)
        if normalized in BANNED_PACKAGES:
            out.append(
                Violation(
                    surface="pyproject",
                    package=normalized,
                    location=f"pyproject.toml {location}",
                    detail=f"declared as {item!r}",
                )
            )
    return out


def _scan_one_file(path: Path) -> list[Violation]:
    """AST-walk a single Python file; return banned-import violations."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except OSError, SyntaxError:
        # Skip unreadable / unparseable files — not our job to fix.
        return []

    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in BANNED_IMPORT_ROOTS:
                    out.append(
                        Violation(
                            surface="import",
                            package=top,
                            location=f"{path}:{node.lineno}",
                            detail=f"import {alias.name}",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module:
                continue
            top = module.split(".", 1)[0]
            if top in BANNED_IMPORT_ROOTS:
                out.append(
                    Violation(
                        surface="import",
                        package=top,
                        location=f"{path}:{node.lineno}",
                        detail=f"from {module} import ...",
                    )
                )
    return out


def scan_imports(roots: list[Path]) -> list[Violation]:
    """Walk every ``*.py`` under ``roots`` and report banned imports.

    Uses ``ast`` rather than regex so we don't false-positive on strings,
    docstrings, or comments. Both ``import x`` and ``from x.y import z``
    are checked against ``BANNED_IMPORT_ROOTS`` on the top-level package.
    """
    violations: list[Violation] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            violations.extend(_scan_one_file(path))
    return violations


def check_llm_plugin_imports(src_root: Path) -> list[Violation]:
    """Return every banned-HTTP-client import inside ``llm_*`` plugins.

    Walks ``src_root/yaya/plugins/llm_*/**/*.py`` and flags any
    top-level ``import httpx`` / ``from httpx import …`` (same for
    ``requests`` and ``aiohttp``). The check is scoped deliberately —
    ``httpx`` lives in the kernel's own deps via ``openai``'s transitive
    graph, and unrelated plugins may use it for non-LLM work (HTTP
    tools, web adapters). Only *LLM* plugins are held to SDK-only use.

    Enforces the v1 llm-provider contract (issue #26).
    """
    violations: list[Violation] = []
    plugins_dir = src_root / "yaya" / "plugins"
    if not plugins_dir.is_dir():
        return violations

    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        if not plugin_dir.name.startswith("llm_"):
            continue
        for path in sorted(plugin_dir.rglob("*.py")):
            violations.extend(_scan_llm_plugin_file(path))
    return violations


def _scan_llm_plugin_file(path: Path) -> list[Violation]:
    """AST-walk one ``llm_*`` plugin file; report banned HTTP imports."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except OSError, SyntaxError:
        return []

    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in LLM_PLUGIN_BANNED_IMPORT_ROOTS:
                    out.append(
                        Violation(
                            surface="llm-plugin-import",
                            package=top,
                            location=f"{path}:{node.lineno}",
                            detail=f"import {alias.name}",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module:
                continue
            top = module.split(".", 1)[0]
            if top in LLM_PLUGIN_BANNED_IMPORT_ROOTS:
                out.append(
                    Violation(
                        surface="llm-plugin-import",
                        package=top,
                        location=f"{path}:{node.lineno}",
                        detail=f"from {module} import ...",
                    )
                )
    return out


def run(repo_root: Path) -> ScanResult:
    """Run all scans rooted at ``repo_root`` and return a combined result."""
    result = ScanResult()
    pyproject_hits = scan_pyproject(repo_root / "pyproject.toml")
    import_hits = scan_imports([repo_root / "src", repo_root / "tests"])
    llm_plugin_hits = check_llm_plugin_imports(repo_root / "src")
    result.violations = pyproject_hits + import_hits + llm_plugin_hits
    result.ok = not result.violations
    return result


def _print_human(result: ScanResult) -> None:
    print("Scanning pyproject.toml deps...")
    pyproject_hits = [v for v in result.violations if v.surface == "pyproject"]
    if not pyproject_hits:
        print("  PASS: no banned packages declared.")
    else:
        for hit in pyproject_hits:
            print(f"  FAIL: {hit.render()}")

    print("Scanning src/ and tests/ imports...")
    import_hits = [v for v in result.violations if v.surface == "import"]
    if not import_hits:
        print("  PASS: no banned imports found.")
    else:
        for hit in import_hits:
            print(f"  FAIL: {hit.render()}")

    print("Scanning src/yaya/plugins/llm_* for raw HTTP clients...")
    llm_hits = [v for v in result.violations if v.surface == "llm-plugin-import"]
    if not llm_hits:
        print("  PASS: llm-provider plugins use SDK only.")
    else:
        for hit in llm_hits:
            print(f"  FAIL: {hit.render()}")

    if result.ok:
        print("Result: PASS")
        print(f"Permitted LLM SDKs: {', '.join(sorted(PERMITTED_LLM_SDKS))}")
    else:
        n = len(result.violations)
        plural = "violation" if n == 1 else "violations"
        print(f"Result: FAIL — {n} {plural}")
        print(DOC_REFERENCE)
        if llm_hits:
            print(LLM_DOC_REFERENCE)


def _print_json(result: ScanResult) -> None:
    payload = {
        "ok": result.ok,
        "violations": [asdict(v) for v in result.violations],
        "permitted_llm_sdks": result.permitted_llm_sdks,
        "banned_packages_count": len(result.banned_packages),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce AGENT.md §4: no third-party agent frameworks.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to the script's parent directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human report.",
    )
    args = parser.parse_args(argv)

    try:
        result = run(args.repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:  # pragma: no cover
        print(f"check_banned_frameworks: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        _print_json(result)
    else:
        _print_human(result)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
