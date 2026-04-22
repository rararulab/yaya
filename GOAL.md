# GOAL.md — yaya

**This is the anchor.** Every scope decision — features, dependencies,
surface area — is checked against this document. If a PR conflicts with
`GOAL.md` and the principle still holds, reject the PR. If the principle
no longer holds, update `GOAL.md` in the **same** PR and justify it.

## North Star

yaya is a **lightweight, kernel-style agent that grows itself.** The
kernel is the product: an event bus, a plugin registry, and a fixed
agent loop that acts as the scheduler. Everything else — **every user
surface, every LLM provider, every tool, every skill, every memory
backend, every next-action strategy** — is a plugin. Running `yaya
serve` boots the kernel and loads the bundled `web` adapter plugin;
users interact through a browser at `http://127.0.0.1:<port>`. yaya
can author and install its own plugins on demand — that is the product.

## Problem

Existing coding agents (Claude Code, Cursor, Aider, Codex) are heavy,
opinionated, and closed. Adding a capability means forking the product or
waiting for upstream. **Users adapt to the agent, not the other way around.**

yaya inverts that: when the user asks for a capability yaya does not have,
yaya writes the plugin, installs it, and the next request uses it.
Self-hosting growth is the product.

## Users & Jobs

- **Primary**: power users and developers who want an agent they can
  extend in minutes, not quarters.
- **Typical session (5 minutes)**: run `yaya serve`, browser opens to
  `http://127.0.0.1:<port>`, describe a task. yaya handles it with
  installed plugins or drafts a new plugin, installs it, reloads, and
  completes the task with the new capability live.
- **Secondary**: plugin authors who want a minimal, stable kernel —
  and a clear plugin protocol — to build against.

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
        │    built-in CLI (self-bootstrap only):  │
        │       serve · version · update · doctor │
        │       · plugin {list, install, remove}  │
        └─────┬─────────┬────────────┬────────────┘
              ▼         ▼            ▼
         STRATEGIES  SKILLS      MEMORY
         (plugins)  (plugins)   (plugins)
         ReAct /    domain-     sqlite / vec / ...
         plan-exec  specific
         / ...
                         │
                         ▼
                   LLM PROVIDERS
                    (plugins)
                openai / anthropic /
                ollama / lmstudio / ...
```

Kernel = OS. Events = syscalls. Plugins = drivers + userland.

## Product Principles (priority order)

1. **Kernel stays small.** The kernel ships only the event bus, the
   plugin registry, the agent loop, and the minimum CLI required to
   bootstrap and manage plugins. **If it could be a plugin, it is a
   plugin** — including every user surface, every LLM provider, every
   strategy, every memory backend.
2. **Adapters are plugins from day 1.** Web, TUI, Telegram, Slack —
   all implement the same adapter contract. `yaya serve` boots the
   kernel and loads the bundled `web` adapter plugin. We dogfood the
   protocol so it is never "special" for core.
3. **Closed event set at 1.0.** A finite, versioned catalog of event
   kinds (see `docs/dev/plugin-protocol.md`).
   Plugin-private payloads use the `x.<plugin>.<kind>` extension
   namespace — they route through the bus but do not pollute the
   public contract.
4. **Agent loop is the scheduler.** Loop shape is fixed and lives in
   the kernel. Per-step decisions (next action, retry, stop condition)
   are delegated to a `strategy` plugin category. ReAct / plan-execute
   / reflexion are each a strategy plugin.
5. **Self-hosting growth.** yaya ships the machinery to extend yaya.
   Plugin authoring is a first-class **agent** capability, not an
   afterthought for humans.
6. **Single-process, local-first.** One Python process. No required
   backend, no account. Default bind: `127.0.0.1`. A remote LLM is a
   provider plugin, not a dependency.
7. **Fail loud, degrade gracefully.** A broken plugin taints only
   itself; the kernel keeps running. The event bus drops that plugin's
   subscriptions and surfaces a `plugin.error` event.
8. **Readable > clever.** A new plugin author understands the kernel
   and the protocol in one sitting.
9. **Ship assets, not toolchains.** The bundled web adapter is
   pre-built to static assets and shipped in the Python wheel. End
   users install with `pip` — no Node, no npm, no browser extension
   at install or run time.

## Non-Goals (explicit)

- **NOT** an IDE or language server. yaya is an agent with pluggable
  surfaces; the default surface is a local web UI.
- **NOT** a hosted web service. Through 1.0, `yaya serve` binds
  `127.0.0.1` only. No public bind flag, no auth layer, no multi-tenant
  mode. Anyone who wants that runs yaya behind their own reverse proxy
  at their own risk.
- **NOT** a cloud service. No hosted control plane, no account, no
  telemetry-by-default.
- **NOT** a general-purpose LLM-app framework. The scope is "an agent
  that grows itself"; the plugin protocol serves that scope, not
  arbitrary orchestration.
- **NOT** an agent runtime with pluggable loop *shapes*. The loop is
  fixed; only its *decisions* are pluggable via strategy plugins.

## Command surface (1.0, kernel built-ins only)

| Command | Purpose |
|---------|---------|
| `yaya serve` | **Default.** Boot kernel; load bundled `web` adapter plugin; open browser. |
| `yaya version` | Print kernel + loaded-plugin versions. |
| `yaya update` | Self-update the yaya binary/wheel. |
| `yaya doctor` | Boot kernel, emit a synthetic round-trip, and print each loaded plugin's `health_check` in a colour-coded table (or JSON). Exit 1 when the round-trip fails or any plugin reports `failed`. |
| `yaya plugin list` | List installed plugins with category and status. |
| `yaya plugin install <src>` | Install a plugin (pip package / path / registry URL). |
| `yaya plugin remove <name>` | Uninstall. |

Everything else — adapters, tools, skills, memory, LLM providers,
strategies — is a plugin. Adding a new built-in subcommand requires a
GOAL.md amendment.

## Plugin categories (1.0 closed set)

| Category | Subscribes to | Emits |
|---|---|---|
| **adapter** | `assistant.message.*`, `tool.call.start` | `user.message.received`, `user.interrupt` |
| **tool** | `tool.call.request` | `tool.call.result` |
| **llm-provider** | `llm.call.request` | `llm.call.response`, `llm.call.error` |
| **strategy** | `strategy.decide.request` | `strategy.decide.response` |
| **memory** | `memory.query`, `memory.write` | `memory.result` |
| **skill** | `user.message.received` (filtered) | any of the above via kernel |

Full event catalog lives in
`docs/dev/plugin-protocol.md` and is the
authoritative 1.0 contract.

## Milestones

- **0.1 — kernel live end-to-end**: kernel (event bus + registry +
  fixed agent loop), plugin protocol v0, bundled plugins covering one
  of each category (web adapter · one LLM provider · one tool · one
  strategy · one memory), `yaya serve` opens a browser chat that
  round-trips a real LLM call through the bus.
- **0.5 — self-authoring plugin**: in the web UI, "I want X" produces
  a `specs/<x>.spec`, scaffolds a plugin (correct category,
  subscribes to the right events), installs it locally, reloads the
  kernel, UI picks up the new capability without restart.
- **1.0 — protocol freeze**: event taxonomy, plugin ABI, strategy
  interface, adapter contract, and the web↔kernel WS schema are all
  frozen. Plugins written against 1.0 keep working across 1.x.
- **2.0 — marketplace + sandbox**: plugin registry (discovery + install
  from a curated index), signed plugins, capability-restricted sandbox
  execution by default.

## Success Metrics

- **Time-to-first-plugin** for a new user: ≤5 minutes from `pip install
  yaya` to a working custom plugin authored through the web UI.
- **Seed release (0.1) plugin count**: exactly one of each category
  (web adapter + 1 LLM provider + 1 tool + 1 strategy + 1 memory).
  Deliberately minimal to prove the protocol.
- **Kernel size budget** (Python LOC in `src/yaya/kernel/` +
  `src/yaya/cli/`, excluding plugins and the web shell): stays under
  <!-- TODO: confirm target, e.g. 2000 LOC --> through 1.0. Enforced in CI.
- **Self-authoring rate**: ≥30% of plugins in the wild authored through
  yaya's own self-authoring loop (health metric for principle #5).
- **Cold start**: `yaya serve` to interactive chat in the browser in
  ≤<!-- TODO: confirm, e.g. 500ms cold, 150ms warm -->.
- **Wheel size**: bundled web assets + Python package stays under
  <!-- TODO: confirm, e.g. 5 MiB --> — enforced in CI on the release wheel.

## Anti-Vision (what yaya refuses to become)

- A monolith where features accumulate in the kernel to "avoid plugin
  boilerplate".
- A kernel with special cases for the bundled plugins. Bundled plugins
  load through the **same protocol** as third-party plugins.
- An agent with a fork of the event bus for "internal events" that
  third-party plugins cannot subscribe to.
- An agent locked to one LLM vendor, one runtime, or one UI.
- A hosted SaaS with accounts, billing, and a control plane.
- A polyglot dual-runtime beast that needs Node **at run time** on the
  user's machine.
- A framework with a glossy docs site and no users.

## Governance of this document

- Changing North Star, Principles, Non-Goals, Anti-Vision, or the
  closed event taxonomy requires owner review and a dedicated issue
  labelled `governance`.
- Milestones, Success Metrics, and the plugin category table may be
  refined in ordinary PRs with justification in the PR body.
- Every `docs/dev/*.md` and folder-local `AGENT.md` must be consistent
  with `GOAL.md`. When they conflict, `GOAL.md` wins — fix the
  downstream doc in the same PR that introduced the conflict.
