# Web Adapter Plugin

The web UI is an **adapter plugin** named `web`, bundled with yaya and
loaded by default when you run `yaya serve`. It is not a kernel
subpackage — it lives under `src/yaya/plugins/web/` and loads through
the same protocol as any third-party adapter (see
[plugin-protocol.md](plugin-protocol.md)). The kernel has no special
case for it.

The browser UI is built on
[`@mariozechner/pi-web-ui`](https://github.com/badlogic/pi-mono/tree/main/packages/web-ui)
(Lit web components + TailwindCSS v4).

## Runtime shape

```
yaya serve
└── one Python process
    ├── kernel boot (bus · registry · agent loop)
    │     └── discover + load bundled plugin "web" (and any others
    │         registered via yaya.plugins.v1 entry point)
    └── "web" adapter plugin started:
          ├── uvicorn + FastAPI (ASGI)
          ├── GET /            → pre-built UI shell (HTML)
          ├── GET /assets/*    → static JS/CSS (importlib.resources)
          ├── WS  /ws          → adapter ↔ kernel bridge (over the event bus)
          └── API /plugins/*   → thin proxies to the registry
```

- Default bind: `127.0.0.1:<port>` (port picked from env / CLI flag /
  first-free). Non-goal: public-internet deployment (see
  [GOAL.md](../goal.md)).
- `yaya serve --no-open` suppresses the automatic browser launch.
- The agent loop runs in the Python **kernel**. The browser is a
  renderer and input device, nothing more. We do **not** embed
  `pi-agent-core` or any JS/TS agent runtime.

## Role in the plugin protocol

The `web` plugin is an `adapter` (see
[plugin-protocol.md](plugin-protocol.md#plugin-categories-closed-set)):

| | |
|---|---|
| Subscribes | `assistant.message.*`, `tool.call.start`, `plugin.*`, `kernel.*` |
| Emits | `user.message.received`, `user.interrupt` |
| ABI extras | `adapter_id = "web"` |

Each WebSocket client gets its own `session_id`. The adapter
translates kernel events into WS frames to the browser and browser
frames back into `user.message.received` events on the bus.

## Source layout

```
src/yaya/plugins/web/
├── pyproject.toml       # ships as a Python subpackage; registers entry point
├── __init__.py          # exposes `plugin: Plugin`
├── server.py            # FastAPI app + WebSocket bridge
├── package.json         # npm: @mariozechner/pi-web-ui (build-time dep)
├── tsconfig.json
├── vite.config.ts
├── src/                 # our shell wiring pi-web-ui to the WS schema
│   ├── main.ts
│   ├── ws-client.ts     # thin WS client speaking the yaya event schema
│   └── components/      # yaya-specific Lit components
├── index.html
└── static/              # *build output* — ships in the wheel
    ├── index.html
    ├── assets/*.js
    └── assets/*.css
```

`static/` is git-tracked so end users who install the wheel get the
UI without Node. CI verifies `static/` is up-to-date with `src/`.

## Dependency policy

- pi-web-ui is consumed **as an npm dependency** (`package.json`) —
  not vendored, not forked. Upgrades go through ordinary `npm update`
  PRs.
- `vendor/pi-mono/` in the repo is **reference only** — a pinned
  mirror for humans and agents to read. Do NOT import from it; the
  build must resolve through npm.
- Peer dependencies (`@mariozechner/mini-lit`, `lit`) live in the
  plugin's own `package.json` and are pinned.

## Build pipeline

yaya ships two toolchains; only the Python half is required at
install time.

- **Install time (user-facing)**: `pip install yaya`. Pure Python.
  The wheel already contains `src/yaya/plugins/web/static/`.
- **Build time (contributor)**: Node + npm. `just web-build` runs
  `npm ci && npm run build` inside `src/yaya/plugins/web/` and writes
  into `static/`. The wheel's build step (`hatchling`) includes
  `static/` as package data.
- **Dev loop**: `just web-dev` starts Vite's dev server with HMR on a
  separate port, proxied by `yaya serve --dev` to the Python kernel.
  Two processes during development, one at release.

### just recipes

```bash
just web-install    # npm ci inside src/yaya/plugins/web
just web-build      # npm run build → static/
just web-dev        # vite dev server (HMR)
just web-check      # biome + tsc --noEmit
```

### CI rules

- `just web-check` runs on every PR.
- `just web-build` runs and CI **fails if `static/` changed** — the
  PR author must commit the built assets alongside source changes.
  Rationale: keeps the wheel reproducible; avoids Node in the
  release pipeline.
- Wheel-size budget (see [GOAL.md](../goal.md) success metrics) is
  asserted on the built artifact.

## WebSocket schema

The WS schema is a thin serialization of the public event set. The
authoritative catalog lives in `src/yaya/kernel/events.py`; the TS
mirror lives at `src/yaya/plugins/web/src/events.ts`. **Any change to
`events.py` updates the TS side in the same PR** — CI compares a JSON
Schema export of the Python catalog against the TS types.

Frames flow in both directions:

| WS frame | Direction | Kernel event |
|---|---|---|
| `{type: "user.message", text, attachments?}` | browser → adapter | `user.message.received` |
| `{type: "user.interrupt"}` | browser → adapter | `user.interrupt` |
| `{type: "assistant.delta", content}` | adapter → browser | `assistant.message.delta` |
| `{type: "assistant.done", content, tool_calls}` | adapter → browser | `assistant.message.done` |
| `{type: "tool.start", id, name, args}` | adapter → browser | `tool.call.start` |
| `{type: "tool.result", id, ok, value?, error?}` | adapter → browser | `tool.call.result` |
| `{type: "plugin.loaded", ...}` | adapter → browser | `plugin.loaded` |
| `{type: "kernel.error", source, message}` | adapter → browser | `kernel.error` |

Extension events (`x.<plugin>.<kind>`) are forwarded transparently so
plugin-private UI surfaces can receive their private events without
kernel involvement.

## Known 0.1 quirk: port handshake

`yaya serve --port <P>` tells the kernel to use port `<P>`, but the
web adapter plugin picks its own port from `YAYA_WEB_PORT` (default:
auto). If you want them aligned, set the env var:

```bash
YAYA_WEB_PORT=7456 yaya serve --port 7456 --no-open
```

A follow-up issue will have `serve` pass its port to the adapter via
config. Until then, check `/api/health` to confirm the actual port.

## What NOT To Do

- Do NOT special-case the web plugin in kernel code. It must register
  and receive events through the same ABI as third-party adapters.
- Do NOT import from `vendor/pi-mono/` — use the npm package.
- Do NOT embed `pi-agent-core` or any JS/TS agent runtime. The agent
  is Python; the browser renders.
- Do NOT add a build step that requires Node **at install time** —
  users get a pre-built wheel.
- Do NOT introduce an auth layer or a public-bind default. That is a
  2.x conversation at earliest.
- Do NOT couple UI components directly to kernel internals — the
  event catalog is the only contract.
