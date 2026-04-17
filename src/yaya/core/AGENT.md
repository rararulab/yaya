# src/yaya/core — Agent Guidelines

## Purpose
Domain logic. Agents, flows, updater, pure computation. Zero CLI dependencies.

## Architecture
- `updater.py` — self-update: version resolution, asset download, checksum, atomic swap. Returns `UpdateStatus` dataclass; no printing.
- Future agent/flow modules live here. Each MUST conform to Oracle Agent Spec — see [../../../docs/dev/agent-spec.md](../../../docs/dev/agent-spec.md).

## Critical Invariants
- **NO imports from `yaya.cli.*`.** Violating this breaks the layering contract.
- All I/O (network, disk) is behind an injectable seam (function arg, default) so tests can substitute. See `tests/conftest.py` for the `STATE_DIR` monkeypatch pattern.
- Functions return structured results (dataclasses, enums) — never print, never `sys.exit`.
- Agent/flow definitions are declared via `PyAgentSpec` types and round-trip through JSON/YAML. Every new agent/flow ships with a conformance test.
- No module-level side effects at import time (network, filesystem writes, env mutation).

## What NOT To Do
- Do NOT hard-code config defaults in Python — use a config file or env.
- Do NOT import runtime-specific adapters (`langgraph.*`, `autogen.*`) here — keep `core/` portable.
- Do NOT add a public function/class without a docstring and a test.
- Do NOT swallow exceptions — propagate or wrap with context.

## Dependencies
- External: `pydantic`, `httpx`, `loguru`.
- Downstream consumers: `yaya.cli.*`.
- See [../../../docs/dev/architecture.md](../../../docs/dev/architecture.md) and [../../../docs/dev/agent-spec.md](../../../docs/dev/agent-spec.md).
