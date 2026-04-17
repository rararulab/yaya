# src/yaya — Agent Guidelines

## Purpose
Package root. Re-exports the public API surface and the CLI entry point.

## Architecture
- `__init__.py` — exposes `__version__` and the public API. Nothing else is importable from outside the package.
- `__main__.py` — `python -m yaya` entry. Delegates to `cli.app`.
- `cli/` — CLI layer (Typer app, commands, output). See [cli/AGENT.md](cli/AGENT.md).
- `core/` — domain logic (agents, flows, updater). See [core/AGENT.md](core/AGENT.md).

## Critical Invariants
- `core/` MUST NOT import from `cli/` — verified by ruff's import rules and tests.
- Every public callable/class has a docstring explaining **why**, not **what**.
- Public API changes require a CHANGELOG entry in the same PR (release-please parses it).

## What NOT To Do
- Do NOT add a new subpackage without its own `AGENT.md` — blocks merge.
- Do NOT add module-level side effects (network, filesystem, env reads) at import time.
- Do NOT bypass `cli/output.py` for stdout — all rendering goes through it.

## Dependencies
- Runtime: `typer`, `rich`, `pydantic`, `pydantic-settings`, `loguru`, `httpx`.
- See [../../docs/dev/architecture.md](../../docs/dev/architecture.md) for the full layering contract.
