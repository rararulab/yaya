# Web Adapter Plugin

The web UI is an **adapter plugin** named `web`, bundled with yaya and
loaded by default when you run `yaya serve`. It is not a kernel
subpackage — it lives under `src/yaya/plugins/web/` and loads through
the same protocol as any third-party adapter (see
[plugin-protocol.md](plugin-protocol.md)). The kernel has no special
case for it.

The browser UI is a Vite-built integration of
[`@mariozechner/pi-web-ui@0.67.6`](https://github.com/badlogic/pi-mono/tree/main/packages/web-ui)
(Lit web components + Tailwind v4). See issue #66 for the landing PR.

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
  renderer and input device, nothing more.

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
├── __init__.py           # entry point exposes `plugin: Plugin`
├── plugin.py             # FastAPI app + WebSocket bridge
├── AGENT.md              # whitelist / blacklist table lives here
├── package.json          # vite + vitest + pi-web-ui + mini-lit + lit + lucide
├── tsconfig.json         # strict TS
├── vite.config.ts        # outDir=static; tools/index.js stub plugin
├── index.html            # /   — mounts <yaya-app> shell
├── src/
│   ├── main.ts
│   ├── app.css           # kimi-style theme tokens + pi-web-ui base
│   ├── app-shell.ts      # <yaya-app> sidebar + hash router
│   ├── chat-shell.ts     # <yaya-chat> chat transcript + empty-state hero
│   ├── settings-view.ts  # <yaya-settings> — LLM / Plugins / Advanced tabs
│   ├── schema-form.ts    # JSON-Schema-driven form (depth 1)
│   ├── store.ts          # createStore<T>() reactive primitive
│   ├── api.ts            # HTTP client for /api/{plugins,config,llm-providers}
│   ├── types.ts          # discriminated-union WS frame types
│   ├── ws-client.ts      # reconnect + send queue
│   ├── stubs/
│   │   └── tools-index.ts
│   └── __tests__/
│       ├── ws-client.test.ts
│       ├── chat-shell.test.ts
│       ├── store.test.ts
│       └── schema-form.test.ts
└── static/               # *build output* — git-tracked, shipped in the wheel
    ├── index.html
    └── assets/
        ├── index-<hash>.js          # entry chunk
        ├── settings-view-<hash>.js  # lazy-loaded settings chunk
        └── index-<hash>.css
```

## Browser routes (kimi-style redesign, issue #108)

The UI is a two-column layout: a collapsible sidebar on the left and
the active route in the main area.

```
┌────────────┬───────────────────────────────────┐
│  SIDEBAR   │            MAIN AREA              │
│            │                                   │
│  ▸ logo    │  #/chat      → <yaya-chat>        │
│  ▸ New chat│    · empty-state hero (wordmark   │
│  ▸ Chat    │      + quick-start chips)         │
│  ▸ Settings│    · streaming bubbles            │
│  ▸ history │    · prompt input pinned bottom   │
│  ▸ theme   │                                   │
│  ▸ version │  #/settings  → <yaya-settings>    │
│            │    tabs:                          │
│            │    · LLM Providers                │
│            │    · Plugins                      │
│            │    · Advanced (raw config)        │
└────────────┴───────────────────────────────────┘
```

Routing is hash-based: `#/chat` (default) and `#/settings`. The
settings module is a dynamic import so chat-only users do not pay
for its bundle — Vite emits it as a separate chunk.

### Extending Settings with a new tab

1. Add a `Tab` variant in `settings-view.ts`
   (e.g. `type Tab = "llm" | "plugins" | "advanced" | "mytab"`).
2. Add a `loaded.mytab = false` field and a `loadTab()` branch that
   fetches from the relevant endpoint, tolerating `404`/`501`.
3. Add a render method (`renderMytab()`) that re-uses
   `renderSchemaForm` for any JSON-Schema-driven fields and the
   shared `.yaya-row` / `.yaya-list` / `.yaya-banner` CSS classes.
4. Append a `renderTab("mytab", "My Tab")` button to the tab bar.
5. If the tab owns a new resource, extend `api.ts` with a typed
   client and add a `vitest` case under `src/__tests__/`.

### Theme

The palette is driven by CSS custom properties (`--yaya-sidebar-bg`,
`--yaya-main-bg`, `--yaya-accent`, `--yaya-text-primary`,
`--yaya-text-secondary`, `--yaya-border`, `--yaya-hover`,
`--yaya-active`). A `prefers-color-scheme: dark` media query flips
them; the footer toggle persists an explicit `.dark` class on
`<html>` in `localStorage` under `yaya.theme`.

### HTTP config API

The settings view consumes the adapter-side HTTP config surface:

| Verb   | Path                              | Purpose                            |
|--------|-----------------------------------|------------------------------------|
| GET    | `/api/plugins`                    | list rows (name, category, status, version, enabled, config_schema, current_config) |
| PATCH  | `/api/plugins/<name>`             | toggle `{enabled}` or patch config |
| POST   | `/api/plugins/install`            | `{source, editable?}` → `{job_id}` |
| DELETE | `/api/plugins/<name>`             | uninstall                          |
| GET    | `/api/config`                     | masked map of all config keys      |
| GET    | `/api/config/<key>?show=1`        | reveal one key                     |
| PATCH  | `/api/config/<key>`               | `{value}`                          |
| DELETE | `/api/config/<key>`               | drop one key                       |
| GET    | `/api/llm-providers`              | provider rows with active flag     |
| PATCH  | `/api/llm-providers/active`       | `{name}` → list                    |
| POST   | `/api/llm-providers/<name>/test`  | `{ok, latency_ms, error?}`         |

The client in `src/api.ts` returns `ApiError { status }` on non-2xx
responses. Tabs render an informational banner when the backend
reports 404/501 so a partial rollout degrades gracefully rather than
breaking the UI.

`static/` is git-tracked so end users who install the wheel get the
UI without Node. CI verifies `static/` matches a fresh Vite build.

## pi-web-ui whitelist / blacklist (Dependency Rule)

`pi-web-ui` ships both pure-presentation components and
use-case-coupled modules that assume the browser owns the agent, API
keys, and session storage. yaya inverts each of those — the Python
kernel owns the agent, env vars hold keys, a future memory plugin
holds sessions. We cherry-pick; we do NOT import the barrel index.

**Whitelist:** `MessageList`, `StreamingMessageContainer`, `Input`,
`ConsoleBlock`. Plus `@mariozechner/mini-lit` primitives (including
`ThemeToggle`), `lit`, `lucide`, and Tailwind via
`@tailwindcss/vite`.

**Blacklist (pre-commit grep enforces in `src/yaya/plugins/web/src/`):**

- The upstream chat panel class (pulls the upstream agent-core runtime)
- The upstream agent interface component
- Custom-provider card / dialog / store
- Api-key prompt dialog, keys tab, keys input, keys store
- Settings dialog / tab, providers-models tab, proxy tab
- Session list dialog, sessions store, app storage helpers,
  IndexedDB backend, persistent storage dialog
- Anything from the sandbox runtime bridge or router
- Everything from `@mariozechner/pi-agent-core`
- Everything from `@mariozechner/pi-ai`

The rationale is lesson 27 in
[docs/wiki/lessons-learned.md](../wiki/lessons-learned.md): a
framework-adjacent library can still contain use-case-level
assumptions disguised as components. The Dependency Rule demands we
evaluate each export, not the library as a unit.

### tools/index.js stub

pi-web-ui's `tools/index.js` side-effect-auto-registers tool
renderers that transitively pull provider SDKs (anthropic, mistral,
openai, google), pdfjs, lmstudio, and ollama. We redirect that
module to `src/stubs/tools-index.ts` via a Vite `resolveId` plugin.
The stub keeps the public API (`renderTool`, `registerToolRenderer`,
`getToolRenderer`, `setShowJsonMode`) as no-ops; the yaya shell
renders tool output through `<console-block>` driven by WS
`tool.start` / `tool.result` frames instead.

## Version pinning

`@mariozechner/pi-web-ui` is pinned to `0.67.6` (exact, no caret).
Upgrades go through a deliberate PR that re-audits the whitelist and
blacklist — the upstream library can add new use-case-coupled
exports between minor versions, and our pre-commit grep cannot catch
new names automatically.

## Build pipeline

yaya ships two toolchains; only the Python half is required at
install time.

- **Install time (user-facing)**: `pip install yaya`. Pure Python.
  The wheel already contains `src/yaya/plugins/web/static/`.
- **Build time (contributor)**: Node 20+ and npm. Inside
  `src/yaya/plugins/web/`:
  ```bash
  npm ci          # install
  npm run check   # tsc --noEmit
  npm run test    # vitest
  npm run build   # vite build → static/
  ```

## CI rules

A dedicated **Web UI** job runs on every PR:

1. `npm ci`
2. `npm run check`
3. `npm run test`
4. `npm run build`
5. Shape-check on the freshly built `static/index.html`: it must
   reference Vite-hashed JS and CSS assets and must not contain
   the old placeholder markers.

Rationale: the wheel needs a pre-built `static/` so `pip install`
users skip Node. Byte-for-byte equality across OS / Node versions
is currently impractical (tiny Tailwind class-scan ordering diffs
between macOS and Linux on the runners), so CI only enforces the
shape of the build. PR authors should still rebuild locally and
commit the result; reviewers can eyeball bundle sizes for
regressions from the build step's `stdout`.

## HTTP admin API

The adapter mounts an HTTP admin API under `/api/` for the browser
UI's control plane (config, plugin, and LLM-provider management).
Full endpoint reference + request / response schemas live in
[`plugin-protocol.md § Web HTTP API`](plugin-protocol.md#web-http-api).
The API is **unauthenticated** — 127.0.0.1-only binding is the
sole authorization through 1.0.

## WebSocket schema

The WS schema is a thin serialization of the public event set. The
authoritative catalog lives in `src/yaya/kernel/events.py`; the TS
mirror lives at `src/yaya/plugins/web/src/types.ts` as a
discriminated union with an exhaustive `assertNever(frame)` switch.
**Any change to `events.py` updates the TS side in the same PR** —
lesson 19 (compile-time enforcement of catalog drift).

Frames flow in both directions:

| WS frame | Direction | Kernel event |
|---|---|---|
| `{type: "user.message", text}` | browser → adapter | `user.message.received` |
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

## Multi-connection fanout integration (#36)

The kernel ships the `SessionContext` / `SessionManager` primitive
in `src/yaya/kernel/session_context.py`. The web adapter will
consume it directly — one `SessionManager` per process; each
WebSocket connection maps to one `Connection` handle.

Forward-compat wire format (finalised by the follow-up PR that
wires the adapter in):

- **Attach**: client opens a WebSocket, sends
  `{"op": "attach", "session_id": "<id>", "since_entry": <int|null>}`.
- **Server ack**: kernel replies with
  `{"op": "attached", "connection_id": "<uuid>"}`. Client stores
  the `connection_id` in `sessionStorage` so a tab reload can
  reattach.
- **Replay**: server pushes each `session.replay.entry` envelope
  as `{"op":"event", ...envelope}`, closed by a
  `session.replay.done` frame.
- **Live**: server pushes every subsequent event through the same
  `{"op":"event", ...envelope}` frame.
- **Heartbeat**: client sends `{"op": "heartbeat"}` every 20 s;
  the kernel refreshes `last_seen`.
- **Detach**: client sends `{"op": "detach"}` or simply closes
  the socket; the adapter calls `SessionManager.detach` either
  way.

The adapter is NOT modified in this PR — only the kernel
primitive and CLI validation land here. The follow-up that plumbs
the WebSocket handler to `SessionManager` reuses the exact shape
above so no fresh protocol freeze is required.

## What NOT To Do

- Do NOT special-case the web plugin in kernel code.
- Do NOT import from `vendor/pi-mono/` — use the npm package.
- Do NOT import from `@mariozechner/pi-agent-core` (full ban per
  `AGENT.md` section 4).
- Do NOT import from `@mariozechner/pi-ai`. Provider SDKs live
  Python-side via the `llm_openai` plugin and its siblings.
- Do NOT import the pi-web-ui barrel index (`"@mariozechner/pi-web-ui"`
  with no subpath). Cherry-pick individual components via the
  `@yaya/pi-web-ui/*` alias in `vite.config.ts`.
- Do NOT add a build step that requires Node **at install time** —
  users get a pre-built wheel.
- Do NOT introduce an auth layer or a public-bind default. That is a
  2.x conversation at earliest.
