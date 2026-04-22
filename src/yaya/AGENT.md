# src/yaya — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Package root. Defines the versioned public API and the CLI entry point.
The core lives under `kernel/`; every user surface and capability is a
plugin under `plugins/`.

## External Reality
- `yaya.__version__` is the release contract — release-please bumps it from Conventional Commits.
- Public exports from `__init__.py` are covered by tests in `tests/` (layout mirrors source).
- Import graph is the contract: `kernel/` MUST NOT import from `cli/`, `plugins/`, or `core/`. `plugins/*` MUST import only from `kernel/`. `just check` (ruff + mypy) verifies.
- The event catalog `kernel/events.py` is authoritative. The TypeScript mirror `plugins/web/src/events.ts` is regenerated in-sync; drift fails CI.

## Constraints
- `__init__.py` — `__version__` + public re-exports. Nothing else importable from outside.
- `__main__.py` — `python -m yaya` shim; delegates to `cli.app`.
- `cli/` — Typer entrypoints: `serve · version · update · doctor · plugin {list,install,remove}`. See [cli/AGENT.md](cli/AGENT.md).
- `kernel/` — event bus, plugin registry, fixed agent loop, plugin ABI, event catalog. Nothing else.
- `plugins/` — bundled plugins (`web` adapter, one LLM provider, one tool, one strategy, one memory). Each loads through the **same protocol** as third-party plugins — no special cases.
- `core/` — shared pure-logic helpers (updater, etc.). See [core/AGENT.md](core/AGENT.md).
- Every public callable/class has a Google-style docstring explaining **why**, not **what**.
- Type bar: `mypy --strict` + the per-error-code enable set documented in `pyproject.toml` (`redundant-expr`, `truthy-bool`, `truthy-iterable`, `unused-awaitable`, `possibly-undefined`, `explicit-override`) plus `disallow_any_unimported`. Loosening any of those settings is a `governance` change.

## Interaction (patterns)
- New subpackage ⇒ ships with its own `AGENT.md` in the same PR; otherwise merge is blocked.
- New public event kind ⇒ governance amendment: update `kernel/events.py`, [`docs/dev/plugin-protocol.md`](../../docs/dev/plugin-protocol.md), and `GOAL.md`'s category table in the same PR.
- Public API change ⇒ CHANGELOG entry + Conventional Commit scope covering the break.
- Do NOT add module-level side effects (network, filesystem, env mutation) at import time.
- Do NOT bypass `cli/output.py` for stdout — all rendering goes through it.
- Do NOT special-case bundled plugins in `kernel/` or `cli/`. They load through the plugin ABI.

## Budget & Loading
- Plugin contract (events, ABI, categories): [../../docs/dev/plugin-protocol.md](../../docs/dev/plugin-protocol.md).
- Layering contract: [../../docs/dev/architecture.md](../../docs/dev/architecture.md).
- BDD contracts: [../../docs/dev/agent-spec.md](../../docs/dev/agent-spec.md).
- Maintenance rule: [../../docs/dev/maintaining-agent-md.md](../../docs/dev/maintaining-agent-md.md).
