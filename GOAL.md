# GOAL.md — yaya

**This is the anchor.** Every scope decision — features, dependencies,
surface area — is checked against this document. If a PR conflicts with
`GOAL.md` and the principle still holds, reject the PR. If the principle
no longer holds, update `GOAL.md` in the **same** PR and justify it.

## North Star

yaya is a **lightweight, kernel-style agent that grows itself.** A single
Python process (`yaya serve`) exposes an event-driven kernel whose
plugins are the only way features get added. The default entrypoint is a
local web UI built with
[`@mariozechner/pi-web-ui`](https://github.com/badlogic/pi-mono/tree/main/packages/web-ui)
that opens in the browser and speaks WebSocket to the kernel. yaya can
author and install its own plugins on demand — that is the product.

## Problem

Existing coding agents (Claude Code, Cursor, Aider, Codex) are heavy,
opinionated, and closed. Adding a capability means forking the product or
waiting for upstream. **Users adapt to the agent, not the other way around.**

yaya inverts that: when the user asks for a capability yaya does not have,
yaya writes the plugin, installs it into itself, and the next request uses
it. Self-hosting growth is the product.

## Users & Jobs

- **Primary**: power users and developers who want an agent they can
  extend in minutes, not quarters.
- **Typical session (5 minutes)**: run `yaya serve`, browser opens to
  `http://127.0.0.1:<port>`, describe a task in chat. yaya either handles
  it with existing plugins or drafts a plugin, installs it, reloads the
  kernel, and completes the task with the new capability live.
- **Secondary**: plugin authors who want a minimal, stable kernel to build
  against without ceremony.

## Product Principles (priority order)

1. **Kernel stays small.** If a feature could be a plugin, it is a plugin.
   The core ships an event bus, a plugin loader, a web-server shell, and
   nothing else.
2. **Self-hosting growth.** yaya ships the machinery to extend yaya.
   Plugin authoring is a first-class **agent** capability, not an
   afterthought for humans.
3. **Single-process, local-first.** One Python process. No required
   backend, no account. Default bind: `127.0.0.1`. A remote LLM is a
   plugin, not a dependency.
4. **Events are the contract.** Plugins subscribe to event types; the
   kernel routes. The web UI is just another subscriber over WebSocket.
   No hidden globals, no direct plugin-to-plugin coupling.
5. **Fail loud, degrade gracefully.** A broken plugin taints only itself;
   the kernel keeps running. The web UI keeps rendering whatever events
   still arrive.
6. **Readable > clever.** A new plugin author understands the kernel in
   one sitting.
7. **Ship assets, not toolchains.** The web UI is pre-built to static
   assets and bundled into the Python wheel. End users install with
   `pip` and need no Node, npm, or browser plugin.

## Non-Goals (explicit)

- **NOT** an IDE or language server. yaya is an agent with a local web UI.
- **NOT** a hosted web service. Through 1.0, `yaya serve` binds
  `127.0.0.1` only. No public-internet deployment, no auth layer, no
  multi-tenant mode. Anyone who wants that runs it behind their own
  reverse proxy at their own risk.
- **NOT** a cloud service. No hosted control plane, no account, no
  telemetry-by-default.
- **NOT** a general-purpose LLM-app framework. The scope is "an agent
  that grows itself", not arbitrary orchestration.
- **NOT** a Claude Code / Cursor replacement for teams that want a
  curated product. yaya is for users who want to own their agent.

## Runtime shape

```
yaya serve
└── one Python process (uvicorn + FastAPI in-process)
    ├── GET /             → pre-built UI shell (HTML)
    ├── GET /assets/*     → static JS/CSS from importlib.resources
    ├── WS  /ws           → kernel event bus ↔ web UI (the contract)
    └── API /plugins/*    → list / install / remove / reload
```

The web UI is **consumed** as `@mariozechner/pi-web-ui` (npm dependency
in `src/yaya/web/package.json`). yaya provides its own shell and talks
to the Python kernel over WebSocket; yaya does **not** embed
`pi-agent-core` or any JS/TS agent runtime.

## Command surface (minimum)

| Command | Purpose |
|---------|---------|
| `yaya serve` | Default. Starts the single-process server; opens browser. |
| `yaya version` | Print version. |
| `yaya plugin list` | List installed plugins. |
| `yaya plugin install <src>` | Install from path / URL / registry. |
| `yaya plugin remove <name>` | Uninstall. |

Everything else — including `hello` and `update` — ships as a plugin, not
a built-in subcommand.

## Milestones

- **0.1 — kernel live in browser** — event bus, plugin loader, `yaya
  serve`, web UI shell rendering plugin output. 2–3 seed plugins
  (`version`, `update`, `hello`) as reference plugins. Each has a
  `specs/<name>.spec.md` contract.
- **0.5 — self-authoring plugin** — in the web UI chat, "I want X"
  produces `specs/<x>.spec.md`, scaffolds a plugin, installs it locally,
  reloads the kernel, UI picks up the new capability without restart.
- **1.0 — stable ABIs frozen** — plugin Python ABI (event shapes,
  lifecycle, permissions) and WebSocket UI protocol are both frozen.
  Plugins and UIs written against 1.0 keep working across 1.x.
- **2.0 — plugin registry + sandboxing** — discovery, install, share;
  plugins run in a capability-restricted sandbox by default.

## Success Metrics

- **Time-to-first-plugin** for a new user: ≤5 minutes from `pip install`
  to a working custom plugin authored through the web UI.
- **Kernel size budget** (Python LOC outside plugins and web shell):
  stays under <!-- TODO: confirm target, e.g. 2000 LOC --> through 1.0.
  Enforced in CI.
- **Self-authoring rate**: ≥30% of plugins in the wild authored through
  yaya's own self-authoring loop (health metric for principle #2).
- **Cold start**: `yaya serve` to interactive chat in the browser in
  ≤<!-- TODO: confirm, e.g. 500ms cold, 150ms warm -->.
- **Wheel size**: bundled web assets + Python package stays under
  <!-- TODO: confirm, e.g. 5 MiB --> — enforced in CI on the release wheel.

## Anti-Vision (what yaya refuses to become)

- A monolith where features accumulate in the kernel to "avoid plugin
  boilerplate".
- An agent locked to one LLM vendor or one runtime.
- A hosted SaaS with accounts, billing, and a control plane.
- A polyglot dual-runtime beast that needs Node **at run time** on the
  user's machine.
- A framework with a glossy docs site and no users.

## Governance of this document

- Changing North Star, Principles, Non-Goals, or Anti-Vision requires
  owner review and a dedicated issue labelled `governance`.
- Milestones and Success Metrics may be refined in ordinary PRs with
  justification in the PR body.
- Every `docs/dev/*.md` and folder-local `AGENT.md` must be consistent
  with `GOAL.md`. When they conflict, `GOAL.md` wins — fix the downstream
  doc in the same PR that introduced the conflict.
