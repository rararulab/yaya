# src/yaya — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Package root. Defines the versioned public API and the CLI entry point.

## External Reality
- `yaya.__version__` is the release contract — release-please bumps it from Conventional Commits.
- Public exports from `__init__.py` are covered by tests in `tests/` (layout mirrors source).
- Import graph is the contract: `kernel/` and `core/` MUST NOT import from `cli/` or `web/`. `just check` (ruff + mypy) verifies.
- The WebSocket event schema (`kernel/events.py`) is the kernel↔UI contract. Drift against `web/src/events.ts` fails CI.

## Constraints
- `__init__.py` — `__version__` + public re-exports. Nothing else importable from outside.
- `__main__.py` — `python -m yaya` shim; delegates to `cli.app`.
- `cli/` — Typer entrypoints (serve / version / plugin). See [cli/AGENT.md](cli/AGENT.md).
- `kernel/` — event bus, plugin loader, event schemas (`events.py` is authoritative).
- `web/` — FastAPI app + WebSocket bridge + pre-built static assets. See [../../docs/dev/web-ui.md](../../docs/dev/web-ui.md).
- `plugins/` — seed plugins shipped in-tree (`version`, `update`, `hello`).
- `core/` — shared pure-logic helpers (updater, etc.). See [core/AGENT.md](core/AGENT.md).
- Every public callable/class has a Google-style docstring explaining **why**, not **what**.

## Interaction (patterns)
- New subpackage ⇒ ships with its own `AGENT.md` in the same PR; otherwise merge is blocked.
- Public API change ⇒ CHANGELOG entry + Conventional Commit scope covering the break.
- Do NOT add module-level side effects (network, filesystem, env mutation) at import time.
- Do NOT bypass `cli/output.py` for stdout — all rendering goes through it.

## Budget & Loading
- Layering contract: [../../docs/dev/architecture.md](../../docs/dev/architecture.md).
- Agent/flow rules: [../../docs/dev/agent-spec.md](../../docs/dev/agent-spec.md).
- Maintenance rule: [../../docs/dev/maintaining-agent-md.md](../../docs/dev/maintaining-agent-md.md).
