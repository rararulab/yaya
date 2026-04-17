# Architecture

Clean-architecture layering. Outer layers depend on inner, never the reverse.

## Runtime

```
yaya serve   (one Python process)
└── uvicorn + FastAPI in-process
    ├── HTTP  /           → pre-built web UI shell
    ├── HTTP  /assets/*   → static JS/CSS (importlib.resources)
    ├── WS    /ws         → kernel event bus ↔ UI (the contract)
    └── API   /plugins/*  → list / install / remove / reload
```

Single process. Default bind `127.0.0.1`. No Node at install or run time —
the web UI is pre-built and bundled in the wheel. See [GOAL.md](../goal.md).

## Source layout

```
src/yaya/
  __init__.py       # versioned public API
  __main__.py       # python -m yaya → cli.app
  cli/              # Typer entrypoints: serve / version / plugin
    commands/       # one file per subcommand
    output.py       # shared rendering helpers (for CLI, not the web UI)
  kernel/           # event bus, plugin loader, event schemas (events.py)
  web/              # FastAPI app + WebSocket bridge + static/ (built assets)
    package.json    # consumes @mariozechner/pi-web-ui from npm
    src/            # TypeScript shell (dev-time only)
    static/         # build output — ships in the wheel
  plugins/          # seed plugins shipped in-tree (version, update, hello)
  core/             # shared pure-logic helpers (updater, etc.)
tests/              # mirrors src/ one-to-one
specs/              # BDD contracts (agent-spec) per feature
```

## Layering rules

- `core/` and `kernel/` have **zero** imports from `cli/` or `web/`.
  Verified by ruff's import rules and tests.
- `kernel/` is the innermost layer. `web/` and `cli/` are both adapters
  that drive the kernel; `plugins/` are subscribers to kernel events.
- Every subpackage under `src/yaya/` has its own `AGENT.md` describing
  its purpose, invariants, and "do not" list
  (see `rararulab/.github/docs/agent-md.md`).
- Every public (non-underscore) callable/class has a docstring explaining
  **why**, not **what**. Private helpers document only non-obvious invariants.
- `src/yaya/__init__.py` exposes the versioned public API; nothing else
  is importable from outside the package.

## Kernel ↔ UI contract

The WebSocket event protocol is the **only** coupling between Python and
the browser. Python is authoritative; the TS types in `web/src/events.ts`
mirror `kernel/events.py` and CI fails on drift. See
[web-ui.md](web-ui.md) for the event catalog.

## Specs live next to code

Every non-trivial feature is backed by a `specs/<slug>.spec.md` BDD
contract verified with [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec).
Scenarios bind to test functions via `Test:` selectors. Run
`agent-spec lifecycle` before commit; CI runs `agent-spec guard` on
staged changes. See [agent-spec.md](agent-spec.md).

## Code style

Python follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
with yaya overlays in [code-comments.md](code-comments.md). Enforced by
`ruff` (lint + format) and `mypy --strict`.

TypeScript under `src/yaya/web/` follows `vendor/pi-mono/AGENTS.md`
conventions (no `any`, no dynamic imports for types, biome for
lint/format). Enforced by `just web-check`.
