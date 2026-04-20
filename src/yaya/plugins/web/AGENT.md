## Philosophy
Bundled `web` adapter plugin. Loads through `yaya.plugins.v1` like any third-party adapter — kernel has no special case. Owns a FastAPI + uvicorn server bound to `127.0.0.1:<port>` and translates between browser WebSocket frames and kernel events on the bus.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) — adapter row + closed event catalog.
- [`docs/dev/web-ui.md`](../../../../docs/dev/web-ui.md) — runtime shape, WS schema, build pipeline.
- Contract: [`specs/plugin-web.spec`](../../../../specs/plugin-web.spec).
- Tests: `tests/plugins/web/`.

## Constraints
- `Category.ADAPTER`, `adapter_id = "web"`, `name = "web"`. `yaya serve._has_web_adapter()` looks for this exact shape.
- Bind `127.0.0.1` only — no `--host` flag through 1.0 (GOAL.md non-goals).
- Port from `YAYA_WEB_PORT` env (or constructor override for tests); `0`/unset asks the OS for a free one.
- Subscribes to: `assistant.message.delta`, `assistant.message.done`, `tool.call.start`, `tool.call.result`, `plugin.loaded`, `plugin.removed`, `plugin.error`, `kernel.ready`, `kernel.shutdown`, `kernel.error`.
- Emits: `user.message.received`, `user.interrupt`.
- Session model: each WS connection gets `session_id = ws-<uuid4[:8]>` at `accept()`. The same id flows into every `user.message.received` the adapter publishes; `loop.py` propagates it through every downstream event; `_deliver()` routes by `ev.session_id` lookup in `_clients: dict[str, set[WebSocket]]`. Unmatched session ids broadcast (kernel-origin events use `session_id="kernel"`).
- HTTP admin API (factored into `api.py`, mounted inside `_build_app`):
  - `GET /api/health` — liveness probe.
  - `GET /api/config`, `GET/PATCH/DELETE /api/config/{key}` — live `ConfigStore` CRUD. Secret-suffix keys (`api_key`, `token`, `secret`, `password`) mask by default; `?show=1` reveals on a single-key read.
  - `GET /api/plugins` — name, category, status, version, `enabled`, `config_schema`, `current_config`. `PATCH /api/plugins/{name}` toggles `enabled` (reload-gated). `POST /api/plugins/install` + `DELETE /api/plugins/{name}` wrap `registry.install` / `registry.remove`.
  - `GET /api/llm-providers` — loaded llm-provider plugins with an `active` flag derived from config key `provider`. `PATCH /api/llm-providers/active` writes that key. `POST /api/llm-providers/{name}/test` fires a one-shot `llm.call.request` for UI "test connection".
  - **Unauthenticated, local-only.** `127.0.0.1` bind is the only authorization through 1.0 (GOAL.md non-goal). Operators fronting yaya with a reverse proxy accept the risk.
  - Kernel-side escape hatches: `ctx.registry` + `ctx.config_store` (mirrors `ctx.bus` / `ctx.session`). Third-party plugins SHOULD NOT rely on these — they exist for the bundled admin UI.
- Self-clean on disconnect (lesson #6): `discard(ws)` then `del self._clients[sid]` if empty.
- Static assets ship in the wheel via `importlib.resources.files("yaya.plugins.web") / "static"` — never `__file__` relative paths.
- `uvicorn.Server.should_exit = True` + `await asyncio.wait_for(task, 3.0)` on `on_unload`; cancel on timeout.
- No `asyncio.Lock` anywhere (lesson #1).

## Interaction (patterns)
- Add a new frame type: extend `_event_to_frame` + the `_handle_frame` dispatch. Keep the mapping table short so diffs with `events.py` stay obvious.
- Add a new HTTP endpoint: attach inside `_build_app`, before the `StaticFiles` mount (the mount is the catch-all `/` route).
- Do NOT reach into `registry.snapshot()` directly — the adapter maintains `_plugin_rows` from `plugin.loaded` / `plugin.removed` events. Kernel internals stay out of plugin code.
- Do NOT import from `vendor/pi-mono/` — reference-only mirror for humans, not a build-time dep.
- Do NOT special-case the web plugin in kernel code. Anything that feels like a special case is a protocol gap — raise an issue instead.

## Surface (kimi-style, issue #108)

Entry element: `<yaya-app>`. Chat view always mounted; settings is a **float modal overlay** (`<yaya-settings-modal>` wrapping a native `<dialog>`) — not a route swap (issue #113). Hash states:

| Hash | Behavior |
|---|---|
| `#/chat` (default) | chat transcript + empty-state hero (wordmark + quick-start chips) + prompt input. |
| `#/settings` | opens the modal over the chat; `<yaya-settings>` (lazy chunk) renders LLM Providers · Plugins · Advanced tabs inside it. Closing the modal rewrites the hash back to `#/chat` via `history.replaceState`. |

Modal pattern: `showModal()` gives us the platform focus trap, ESC handling, and inert backdrop. Backdrop click is detected by `event.target === dialogEl`. The modal dispatches a bubbling `yaya:settings-close` event on native `close` so the shell can clear the hash.

Sidebar (≤240 px, collapsible to 48 px icon rail via hamburger toggle, persisted to `localStorage["yaya.sidebar.collapsed"]`): hamburger · logo · New chat · Chat nav · recent-chat list · **connection-status dot · gear settings button (footer-left) + theme toggle + version (footer-right)**. The status dot maps `connecting` / `reconnecting` → amber, `connected` → green, `disconnected` → red; `<yaya-chat>` publishes transitions via the window-level `yaya:connection-status` CustomEvent so the sidebar stays decoupled from the WS client. The `@media (prefers-reduced-motion: reduce)` rule disables the 200ms layout transition. Theme palette driven by CSS custom properties with a `prefers-color-scheme: dark` media-query override and an explicit `.dark` toggle on `<html>`.

Settings tabs consume PR B's HTTP config surface:

| Tab | Endpoints | Notes |
|---|---|---|
| LLM Providers | `GET/PATCH /api/llm-providers`, `POST /api/llm-providers/<name>/test` | radio-switch active provider; per-provider schema-driven config; test-connection button. |
| Plugins | `GET/PATCH/DELETE /api/plugins`, `POST /api/plugins/install` | enabled toggle + schema-driven config; install modal. |
| Advanced | `GET/PATCH/DELETE /api/config`, `GET /api/config/<key>?show=1` | raw config grid; prefix filter; secret reveal toggle. |

The schema-driven form (`src/schema-form.ts`) handles shallow JSON Schema (string / integer / number / boolean / array / object) and auto-detects secret fields by suffix (`_key`, `_token`, `_secret`, `_password`) — password input + reveal toggle. Missing schema falls back to a generic key-value grid.

State management: `src/store.ts` — a tiny `createStore<T>()` with `{get, set, patch, subscribe}`. No framework; no Redux; no Vue/React.

## UI bundle
The `static/` directory is the output of `vite build` inside this folder, a Vite-built integration of `@mariozechner/pi-web-ui@0.67.6`. End users still install via `pip` — Node is a contributor-only dependency. CI rebuilds `static/` and fails if the fresh output differs from the tracked bundle, keeping the wheel reproducible. Vite splits the settings view into a separate chunk (`settings-view-<hash>.js`) so chat-only users do not pay for its bundle.

### pi-web-ui whitelist / blacklist (lesson 27)

`pi-web-ui` ships both pure-presentation components and use-case-coupled modules that assume the browser owns the agent, API keys, and session storage. We cherry-pick; we do not import the barrel index.

**Whitelist (import freely):**

| Export | Role |
|---|---|
| `MessageList` | scrolling transcript |
| `StreamingMessageContainer` | in-flight assistant bubble |
| `ConsoleBlock` | tool stdout/stderr renderer |
| `MessageEditor`, `AttachmentTile`, `ExpandableSection`, `ThinkingBlock` | available if needed; audit deps before using |

**Blacklist (never import — pre-commit grep enforces):**

| Export | Why |
|---|---|
| The upstream chat panel class | pulls the upstream agent-core runtime |
| The upstream agent interface component | same |
| The custom-provider card/dialog/store | browser-side provider routing |
| Api-key prompt dialog, keys tab, keys input, keys store | API keys in browser |
| Settings dialog/tab, providers-models tab, proxy tab | browser-side settings |
| Session list dialog, sessions store, app storage helpers, IndexedDB backend, persistent storage dialog | browser-side session persistence |
| Anything from the sandbox runtime bridge or router | artifact sandbox in browser |
| Everything from `@mariozechner/pi-agent-core` | full ban per `AGENT.md` section 4 |
| Everything from `@mariozechner/pi-ai` except `type Model` (and we do not need it) | provider SDKs; keys live Python-side |

Also permitted: `@mariozechner/mini-lit` primitives, `lit` + directives, `lucide`, Tailwind via `@tailwindcss/vite`, and `localStorage` for the theme preference (lesson 27 classifies this as framework-ring-trivially-replaceable).

The build aliases `tools/index.js` (pi-web-ui's side-effecting auto-register module) to a local stub because that module transitively pulls banned provider SDKs. The stub keeps the public API (`renderTool`, `registerToolRenderer`, `getToolRenderer`, `setShowJsonMode`) as no-ops; our shell renders tool output via `<console-block>` directly from WS `tool.*` frames.

### TypeScript / build layout

```
src/yaya/plugins/web/
├── package.json        # vite + vitest + pi-web-ui + mini-lit + lit + lucide
├── tsconfig.json       # strict; noUncheckedIndexedAccess; experimentalDecorators for Lit
├── vite.config.ts      # outDir=static; stub-plugin for tools/index.js
├── index.html          # /
├── src/
│   ├── main.ts         # bootstrap
│   ├── app.css         # kimi theme tokens + pi-web-ui base
│   ├── app-shell.ts    # <yaya-app> sidebar + hash router (kimi layout)
│   ├── chat-shell.ts   # <yaya-chat> chat transcript + empty-state hero; multiline textarea, Cmd/Ctrl+Enter submits (#115)
│   ├── settings-view.ts# <yaya-settings> tabs (lazy chunk)
│   ├── schema-form.ts  # JSON-Schema-driven form (depth 1)
│   ├── store.ts        # createStore<T>() reactive primitive
│   ├── api.ts          # HTTP client for /api/{plugins,config,llm-providers}
│   ├── types.ts        # discriminated-union WS frame types (mirror events.py)
│   ├── ws-client.ts    # reconnect + send queue
│   ├── stubs/
│   │   └── tools-index.ts  # pi-web-ui tool-register stub
│   └── __tests__/
│       ├── ws-client.test.ts
│       ├── chat-shell.test.ts
│       ├── store.test.ts
│       ├── schema-form.test.ts
│       ├── settings-modal.test.ts
│       └── sidebar.test.ts
└── static/             # vite build output — git-tracked, chunk-split
```

Scripts: `npm run check` (tsc --noEmit), `npm run test` (vitest), `npm run build` (vite). CI runs all three plus `git diff --exit-code static/`.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#plugin-categories-closed-set) + [`docs/dev/web-ui.md`](../../../../docs/dev/web-ui.md).
- Lessons that apply: #1 (no asyncio.Lock), #6 (self-clean dicts), #10 (silent drops log WARNING), #15 (log on unexpected drops), #21 (tight pragma scope).
