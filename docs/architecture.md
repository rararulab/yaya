# Architecture

Clean-architecture layering. Outer layers depend on inner, never the reverse.

```
src/yaya/
  cli/        # entrypoints (Typer/Click commands, I/O, formatting)
    commands/ # one file per subcommand
    output.py # shared rendering helpers
  core/       # domain: agents, flows, updater, pure logic
tests/        # mirrors src/ one-to-one
  cli/        # command-level tests
  core/       # unit tests for domain
```

## Rules

- `core/` has **zero** imports from `cli/`. Violations fail `mypy`/`ruff`.
- Every subpackage under `src/yaya/` has its own `AGENT.md` describing its
  purpose, invariants, and "do not" list
  (see `rararulab/.github/docs/agent-md.md`).
- Every public (non-underscore) callable/class has a docstring explaining
  **why**, not **what**. Private helpers document only non-obvious invariants.
- `src/yaya/__init__.py` exposes the versioned public API; nothing else is
  importable from outside the package.

## Agents and flows

Anything under `core/` that defines an agent or flow MUST conform to
Oracle Agent Spec — see [agent-spec.md](agent-spec.md).
