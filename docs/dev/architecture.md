# Architecture

yaya is a small kernel with plugins orbiting it. The kernel owns the
event bus, the plugin registry, and a fixed agent loop. **Every user
surface, every LLM provider, every tool, every skill, every memory
backend, every strategy is a plugin** — including the ones we bundle.
See [GOAL.md](../goal.md) for the product anchor and
[plugin-protocol.md](plugin-protocol.md) for the authoritative contract.

## Runtime shape

```
                ADAPTERS                      TOOLS
             (plugins)                     (plugins)
          web / tui / tg                bash / fs / http ...
                │                             ▲
                ▼                             │
        ┌───────────── KERNEL (yaya) ─────────────┐
        │    event bus  ·  plugin registry        │
        │    agent loop (the scheduler)           │
        │    built-in CLI: serve / version /      │
        │      update / hello / plugin {...}      │
        └─────┬─────────┬────────────┬────────────┘
              ▼         ▼            ▼
         STRATEGIES  SKILLS      MEMORY
         (plugins)  (plugins)   (plugins)
                         │
                         ▼
                   LLM PROVIDERS
                    (plugins)
```

`yaya serve` = kernel boots → loads bundled `web` adapter plugin →
opens browser at `http://127.0.0.1:<port>`. One Python process.
Default bind `127.0.0.1`. No Node at install or run time — the web
adapter's UI assets are pre-built and shipped in the wheel.

## Source layout

```
src/yaya/
  __init__.py         # versioned public API
  __main__.py         # python -m yaya → cli.app
  cli/                # Typer entrypoints: serve / version / update / hello / plugin
    commands/         # one file per subcommand
    output.py         # shared rendering helpers (CLI only — not web UI)
  kernel/             # the kernel — the entire product core lives here
    bus.py            # event bus: pub/sub, ordering, backpressure
    registry.py       # plugin discovery, lifecycle, failure accounting
    loop.py           # fixed agent loop; calls out to strategy / llm / tool / memory
    plugin.py         # Plugin ABI (Protocol), KernelContext, Category enum
    events.py         # closed event-kind catalog + TypedDict payloads (authoritative)
  plugins/            # bundled plugins — load through the same protocol as third-party
    web/              # web adapter (FastAPI WS bridge + pi-web-ui static assets)
      package.json    # npm: consumes @mariozechner/pi-web-ui
      src/            # TypeScript shell (dev-time only)
      static/         # build output — ships in the wheel
    llm_openai/       # seed llm-provider plugin
    tool_bash/        # seed tool plugin
    strategy_react/   # seed strategy plugin
    memory_sqlite/    # seed memory plugin
  core/               # shared pure-logic helpers (updater, etc.)
tests/                # mirrors src/ one-to-one
specs/                # BDD contracts (agent-spec) per feature
```

## Layering rules

- `kernel/` has **zero** imports from `cli/`, `plugins/`, or `core/`.
  It defines the protocol; everything else depends on it.
- `plugins/*` import from `kernel/` only. Cross-plugin communication
  **must** go through events — no direct Python imports between plugin
  subpackages.
- `cli/` imports from `kernel/` (to boot it) and from `core/` (shared
  helpers). Never from `plugins/*`.
- `core/` is the shared utility layer; it has no kernel knowledge.
- Every subpackage under `src/yaya/` has its own `AGENT.md`.

## Kernel ↔ adapter contract

Adapters speak the public event set (see
[plugin-protocol.md](plugin-protocol.md)). The web adapter layers a
WebSocket protocol on top of those events — the WS schema is a thin
serialization of the event catalog, and mismatches fail CI.

## Specs live next to code

Every non-trivial feature is backed by a `specs/<slug>.spec` BDD
contract verified with [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec).
Scenarios bind to test functions via `Test:` selectors. Run
`agent-spec lifecycle` before commit; CI runs `agent-spec guard` on
staged changes. See [agent-spec.md](agent-spec.md).

## Code style

Python follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
with yaya overlays in [code-comments.md](code-comments.md). Enforced by
`ruff` (lint + format) and `mypy --strict`.

TypeScript under `src/yaya/plugins/web/` follows
`vendor/pi-mono/AGENTS.md` conventions (no `any`, no dynamic imports
for types, biome for lint/format). Enforced by `just web-check`.
