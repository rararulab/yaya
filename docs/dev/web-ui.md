# Web UI

yaya's default surface is a local web UI served by `yaya serve` from a
single Python process. The UI is built on
[`@mariozechner/pi-web-ui`](https://github.com/badlogic/pi-mono/tree/main/packages/web-ui)
(Lit web components + TailwindCSS v4).

## Runtime shape

```
yaya serve
└── one Python process (uvicorn + FastAPI)
    ├── GET /             → pre-built UI shell (HTML)
    ├── GET /assets/*     → static JS/CSS (importlib.resources)
    ├── WS  /ws           → kernel event bus ↔ UI (the contract)
    └── API /plugins/*    → list / install / remove / reload
```

- Default bind: `127.0.0.1:<port>` (port picked from env / CLI flag /
  first-free). Non-goal: public-internet deployment (see
  [GOAL.md](../goal.md)).
- `yaya serve --no-open` suppresses the automatic browser launch.
- The agent loop runs in Python. The browser is a **renderer and input
  device**, not an agent runtime. We do **not** embed `pi-agent-core` or
  any JS/TS agent.

## Source layout

```
src/yaya/web/
├── package.json          # npm workspace — declares @mariozechner/pi-web-ui
├── tsconfig.json
├── vite.config.ts
├── src/                  # our shell: wires pi-web-ui components to the WS event bus
│   ├── main.ts
│   ├── ws-client.ts      # thin WebSocket client speaking yaya's event protocol
│   └── components/       # yaya-specific Lit components composing pi-web-ui primitives
├── index.html
└── static/               # *build output* — do NOT edit by hand
    ├── index.html
    ├── assets/*.js
    └── assets/*.css
```

`static/` is git-tracked so end users who install the wheel get the UI
without needing Node. CI verifies `static/` is up-to-date with `src/`
(see below).

## Dependency policy

- pi-web-ui is consumed **as an npm dependency** (`package.json`) — not
  vendored, not forked. Upgrades go through ordinary `npm update` PRs.
- `vendor/pi-mono/` in the repo is **reference only** — a pinned mirror
  for agents and humans to read. Do NOT import from it; the build must
  resolve through npm.
- Peer dependencies (`@mariozechner/mini-lit`, `lit`) live in yaya's own
  `package.json` and are pinned.

## Build pipeline

yaya ships two toolchains; only the Python half is required at install
time.

- **Install time (user-facing)**: `pip install yaya`. Pure Python. The
  wheel already contains `src/yaya/web/static/`.
- **Build time (contributor)**: Node + npm. `just web-build` runs
  `npm ci && npm run build` inside `src/yaya/web/` and writes into
  `static/`. The wheel's build step (`hatchling`) includes `static/`
  as package data.
- **Dev loop**: `just web-dev` starts Vite's dev server with HMR on a
  separate port, proxied by `yaya serve --dev` to the Python backend.
  Two processes during development, one at release.

### just recipes

```bash
just web-install    # npm ci inside src/yaya/web
just web-build      # npm run build → static/
just web-dev        # vite dev server (HMR)
just web-check      # biome + tsc --noEmit
```

### CI rules

- `just web-check` runs on every PR.
- `just web-build` runs and CI **fails if `static/` changed** — i.e. the
  PR author must commit the built assets alongside source changes.
  Rationale: keeps the wheel reproducible; avoids Node in the release
  pipeline.
- Wheel-size budget (see [GOAL.md](../goal.md) success metrics) is
  asserted on the built artifact.

## WebSocket event protocol (overview)

The contract between kernel and UI is a stream of events. Shapes live in
`src/yaya/kernel/events.py` (Python, authoritative) and
`src/yaya/web/src/events.ts` (TypeScript, generated or hand-maintained
in lock-step).

**Mandatory pairing**: any change to `events.py` updates the TS side in
the same PR. A mismatch fails CI (checksum compare between Python JSON
schema export and the TS type file).

Minimal event kinds:

| Kind | Direction | Payload |
|---|---|---|
| `user.message` | UI → kernel | `{ text, attachments? }` |
| `assistant.message.delta` | kernel → UI | streaming text chunk |
| `assistant.message.done` | kernel → UI | final message + tool calls |
| `tool.call.start` | kernel → UI | `{ id, name, args }` |
| `tool.call.result` | kernel → UI | `{ id, ok, value \| error }` |
| `plugin.installed` | kernel → UI | `{ name, version }` |
| `plugin.reloaded` | kernel → UI | `{ names }` |
| `kernel.error` | kernel → UI | `{ source, message }` |

Full catalog with JSON Schema lives next to `events.py` and is surfaced
in [agent-spec.md](agent-spec.md) contracts.

## What NOT To Do

- Do NOT import from `vendor/pi-mono/` — use the npm package.
- Do NOT embed `pi-agent-core` or any JS/TS agent runtime. The agent is
  Python; the browser renders.
- Do NOT add a build step that requires Node **at install time** —
  users get a pre-built wheel.
- Do NOT introduce an auth layer or a public-bind default to "make it
  easier to deploy". That is a 2.x conversation at earliest.
- Do NOT couple UI components directly to kernel internals — the
  WebSocket event protocol is the only contract.
