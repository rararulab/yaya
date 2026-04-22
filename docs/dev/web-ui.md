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
the chat view in the main area. Settings is a float **modal overlay**
(issue #113) above the chat — not a route swap.

```
┌────────────┬───────────────────────────────────┐
│  SIDEBAR   │            MAIN AREA              │
│            │                                   │
│  ▸ logo    │  #/chat      → <yaya-chat>        │
│  ▸ New chat│    · empty-state hero (wordmark   │
│  ▸ Chat    │      + quick-start chips)         │
│  ▸ history │    · streaming bubbles            │
│  ▸ theme   │    · prompt input pinned bottom   │
│  ▸ ⚙ gear  │                                   │
│  ▸ version │  #/settings  → modal opens over   │
│            │                chat with tabs:    │
│            │    · Plugins                      │
│            │    · Advanced (raw config)        │
└────────────┴───────────────────────────────────┘
```

Routing is hash-based: `#/chat` (default) and `#/settings`. The
settings module is a dynamic import so chat-only users do not pay
for its bundle — Vite emits it as a separate chunk.

### Settings modal semantics (issue #113)

- `<yaya-settings-modal>` owns a native `<dialog>` element and calls
  `showModal()` for the built-in focus trap, ESC handling, and inert
  backdrop. The dialog's `::backdrop` pseudo-element paints the scrim.
- Backdrop clicks are detected by comparing `event.target` to the
  dialog element — clicks inside the card bubble from inner children
  and never match the dialog itself.
- The modal dispatches a bubbling `yaya:settings-close` event on
  `close` (native dialog close). `<yaya-app>` listens and rewrites the
  URL hash back to `#/chat` via `history.replaceState` so the modal
  close does not push a history entry.
- The sidebar gear button in `.yaya-sidebar-footer` opens the modal;
  the old `Settings` nav item is removed. `#/settings` on page load
  still deep-links into the modal (queued in a microtask so the modal
  element is registered first).

### Chat input keybindings (issue #115)

The chat composer is an auto-growing `<textarea>` (`.yaya-input`) with
`rows=1` starting height and a 240 px cap; past the cap the internal
`overflow-y: auto` takes over. `chat-shell.ts` detects macOS at module
load via `navigator.platform` (with a `userAgent` fallback) and stores
it in `IS_MAC`.

| Keys | Behaviour |
|------|-----------|
| **Cmd+Enter** (macOS) / **Ctrl+Enter** (other) | Submit. `preventDefault()`; clears value + resets height. |
| **Enter** | Native newline (no JS). |
| **Shift+Enter** | Native newline (no JS). |

A small hint line below the textarea shows the active modifier label.
The submit button stays for click submission and is disabled while a
turn is in flight.

### Sidebar collapse + connection status (issue #114)

- A hamburger button in `.yaya-sidebar-top` flips `<yaya-app>`'s
  `sidebarCollapsed` state, swapping the grid column between 240px and
  48px via the `.yaya-app.is-collapsed` class. The layout transition
  is `200ms ease`; `@media (prefers-reduced-motion: reduce)` disables
  it. State persists to `localStorage["yaya.sidebar.collapsed"]`.
- Sidebar-footer `.yaya-sidebar-status` renders a `<span class="yaya-status-dot">`
  whose color is driven by the `data-state` attribute:
  `connected` → green, `connecting`/`reconnecting` → amber,
  `disconnected` → red. The initial state is `connecting` so the
  handshake never flashes red.
- `<yaya-chat>` publishes transitions via a window-level
  `yaya:connection-status` CustomEvent (`detail: { status }`). The
  shell subscribes once in `connectedCallback` so the sidebar stays
  decoupled from the WS client and tests can drive the dot without
  standing up a fake WebSocket.

### Plugins tab (unified surface, issues #141 · #143)

One tab per plugin. Before #141 there was a dedicated "LLM Providers"
tab that duplicated llm-provider configuration — users got confused
between surfaces and the Plugins tab's writes were a silent no-op
(wrong namespace). #141 deleted the duplicate; #143 reintroduced full
multi-instance management **inside** the Plugins tab so we keep the
single-tab UX without losing the add/delete/rename features.

For `category === "llm-provider"` plugins, expanding `configure` shows
a list of all instances backed by that plugin (`providers.<id>.*`
rows from `/api/llm-providers`), each with active-radio · label · id
· status dot · Test connection · configure · Delete. A `+ Add
instance` button opens a modal that auto-suggests the next free id
and renders the plugin's JSON Schema for initial config.

Non-llm-provider plugins render a flat schema-form under `configure`
and save to `plugin.<name>.<field>`.

```
┌─ Plugins ─────────────────────────────────────────────────────┐
│ [+ Install]                                                   │
│                                                               │
│ agent-tool     v0.1.0 · tool         [loaded] ☑ enabled       │
│   [configure] [Remove]                                        │
│                                                               │
│ llm-openai     v0.1.0 · llm-provider [loaded] ☑ enabled       │
│   2 instances   [collapse] [Remove]                           │
│   ┌─────────────────────────────────────────────────────────┐ │
│   │ (•) llm-openai (default) llm-openai   ● [Test][…][Del]  │ │
│   │ ( ) Azure OpenAI Prod    llm-openai-2 ● [Test][…][Del]  │ │
│   │     ┌─ expanded row body (Azure OpenAI Prod) ─────────┐ │ │
│   │     │ Label   [ Azure OpenAI Prod                  ] │ │ │
│   │     │ Api Key [ ••••••••••••••••           ] [show] │ │ │
│   │     │ Base Url[ https://…                          ] │ │ │
│   │     │ Model   [ gpt-4o-mini                        ] │ │ │
│   │     │              [Save] [Reset]                    │ │ │
│   │     └─────────────────────────────────────────────────┘ │ │
│   │ [+ Add instance]                                        │ │
│   └─────────────────────────────────────────────────────────┘ │
│                                                               │
│ memory-sqlite  v0.1.0 · memory       [loaded] ☑ enabled       │
│   [configure] [Remove]                                        │
└───────────────────────────────────────────────────────────────┘
```

- **Active radio** on an instance fires `PATCH /api/llm-providers/active`
  with `{name: <id>}` (body key still `name` for PR #110 compat; value
  is an instance id post-D4c).
- **Status dot** maps the most recent Test-connection outcome: green
  (connected), red (failed, error tooltip), grey (untested).
- **Save** on an instance PATCHes only the fields that diverge from
  the server row — partial-write blast-radius minimiser.
- **Delete** opens a confirm dialog; 409 (active / last-of-plugin safety)
  surfaces as a `.yaya-row-error` on the row, not a banner.
- **Test connection** calls `POST /api/llm-providers/<id>/test`; a
  successful Save invalidates any prior result so operators re-test.
- **+ Add instance** opens a modal with auto-suggested id (`<plugin>` →
  `<plugin>-<N>` when taken), client-side validation via
  `isValidInstanceId`, optional label, and the backing plugin's JSON
  Schema rendered inline. 400/409 render inside the modal.
- **Plugin-level toggle** patches `{enabled}` via
  `PATCH /api/plugins/<name>`. **Remove** calls
  `DELETE /api/plugins/<name>` with a native confirm
  prompt.

### Extending Settings with a new tab

1. Add a `Tab` variant in `settings-view.ts`
   (e.g. `type Tab = "plugins" | "advanced" | "mytab"`).
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
| GET    | `/api/llm-providers[?show=1]`     | instance rows `{id, plugin, label, active, config, config_schema}` (D4c) |
| POST   | `/api/llm-providers`              | `{plugin, id?, label?, config?}` → 201 + row (D4c) |
| PATCH  | `/api/llm-providers/<id>`         | `{label?, config?}` partial merge (D4c) |
| DELETE | `/api/llm-providers/<id>`         | 204; 409 when active / last-of-plugin (D4c) |
| PATCH  | `/api/llm-providers/active`       | `{name: <id>}` → list (body key kept for compat) |
| POST   | `/api/llm-providers/<id>/test`    | `{ok, latency_ms, error?}`         |

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

**Whitelist:** `MessageList`, `StreamingMessageContainer`,
`ConsoleBlock`. Plus `@mariozechner/mini-lit` primitives (including
`ThemeToggle`), `lit`, `lucide`, and Tailwind via
`@tailwindcss/vite`. The chat input is a plain
auto-growing `<textarea>` (see "Chat input keybindings"); pi-web-ui's
`Input` was removed in #115 when we adopted multiline + Cmd/Ctrl+Enter.

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

## Session resume (#159)

Clicking a sidebar **Recent** row resumes the underlying persisted
tape rather than starting a fresh session. The flow is:

1. The row's `data-session-id` carries the value from
   `SessionInfo.session_id` (the hashed tape suffix).
2. The shell updates `location.hash` to `#/chat/<id>` via
   `history.replaceState` so a reload sticks with the thread, then
   dispatches a `yaya:resume-session` custom event.
3. `<yaya-chat>` clears its `messages` / `pendingToolCalls` /
   `streamingMessage` / `inFlight` state, fetches
   `GET /api/sessions/<id>/frames`, and applies each frame through
   the same reducer the live WebSocket uses. Historical tool calls
   land as collapsible cards sourced from the tape's `tool_call` /
   `tool_result` entries. Pre-compaction entries are skipped — the
   next turn's prompt already carries the summary through the
   agent-loop projection.
4. The existing WebSocket is torn down and a new one opens at
   `/ws?session=<id>`. The adapter's handshake binds the connection
   to `<id>` so downstream kernel events route to the same tape the
   sidebar row points at. Unknown ids fall back to a fresh
   `ws-<uuid>` and log at INFO — a stale tab never 500s the adapter.
5. On the next user turn the agent loop hydrates prior messages
   from the tape (PR #158) and the thread continues seamlessly.

Two endpoints feed this flow:

* `GET /api/sessions/{id}/messages` is the **agent-loop** projection
  (`project_entries_to_messages` in `yaya.kernel.loop`) — stable
  `{role, content}` shape consumed by cross-turn hydration in #158.
  Do not re-shape it for UI reasons.
* `GET /api/sessions/{id}/frames` is the **UI** projection (#162) —
  a discriminated frame list matching the live WebSocket wire
  (`assistant.done` / `tool.start` / `tool.result` / `user.message`)
  so the chat-shell replays history through the same reducer that
  handles live events. `Observation: ...` user messages (if any ever
  land on tape) are filtered to avoid duplicating the tool card.

On page load, `<yaya-chat>.connectedCallback` inspects
`location.hash` and auto-resumes `#/chat/<id>` when present — the
same code path as the click handler. Fetch failures surface a toast
and fall back to a fresh chat, so deleting a tape while a tab is
open does not leave the UI stuck.

### Provider check on resume (#163)

Each turn the agent loop stamps a `turn/provider` anchor on the
tape with `state={"provider": <instance_id>, "model": <str>}` —
once per turn, not per LLM call. `SessionStore.list_sessions` and
a new `GET /api/sessions/{id}` endpoint surface the latest anchor's
provider and model on `SessionInfo`.

Before hydration on resume, the chat-shell calls `GET /api/sessions/{id}`.
When the historical provider is no longer listed in
`ProvidersView.list_instances()`, the UI renders an inline banner
(not a blocking modal) above the chat pane: "This chat was started
with <old>:<model>; that provider is no longer configured.
Continue with <current active>?" Two buttons — "Continue" and
"Cancel" — both with explicit intent; no silent switch. Legacy
tapes predating the anchor return `provider: null` and suppress
the banner entirely (backward compat).

## Session CRUD (#161)

Each **Recent** row exposes a ⋯ button revealing Rename and Delete
actions; right-click is deliberately not used so the affordance stays
keyboard-reachable.

- **Rename** replaces the row label with an inline `<input>`.
  ``Enter`` commits via `PATCH /api/sessions/{id}` with
  `{name: string}`; ``Escape`` cancels; `blur` commits as well. The
  backend appends a `session/renamed` anchor carrying
  `state={"name": <stripped>}`. `SessionStore.list_sessions` walks
  anchors in tape order and returns the most recent rename's name as
  `SessionInfo.name`, so renames persist across process restart and
  multiple renames stack (newest wins).
- **Delete** swaps the ⋯ button for a `Delete?` confirm chip. Clicking
  it sends `DELETE /api/sessions/{id}`, which calls
  `SessionStore.archive`: the current entries dump to
  `<tapes_dir>/.archive/<tape>.jsonl.<stamp>.bak` (recoverable from
  disk) and the live jsonl file is unlinked. No hard-delete surface is
  exposed to the UI. When the deleted row matches the hash-encoded
  active session, the sidebar resets the chat pane and reopens the WS
  without `?session=` so the user lands on a fresh thread.

The sidebar label priority is `name → preview → last_anchor → id`.

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
