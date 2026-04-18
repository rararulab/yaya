spec: task
name: "plugin-web"
tags: [plugin, adapter, web]
---

## Intent

The bundled `web` adapter plugin is the default yaya user surface.
It loads through the same `yaya.plugins.v1` entry-point ABI as any
third-party adapter — the kernel has no special case for it. On
`on_load` it starts an in-process uvicorn + FastAPI server bound to
`127.0.0.1:<port>`, serving a pre-built static UI, a read-only
`/api/plugins` snapshot, and a `/ws` WebSocket that bridges browser
frames to kernel events. On `on_unload` it stops uvicorn within a
3-second budget. Per-connection WebSocket session ids
(`ws-<uuid4[:8]>`) flow through every event the adapter publishes,
so the kernel's agent loop can route downstream events back to the
correct browser. Events whose session id does not match any
connected WS (notably kernel-origin events on `session_id="kernel"`)
broadcast to every connected client.

## Decisions

- **No kernel special case.** The plugin registers via
  `[project.entry-points."yaya.plugins.v1"]` alongside the other
  bundled plugins; `yaya serve._has_web_adapter()` finds it by the
  generic pattern `category == "adapter" and name.startswith("web")
  and status == "loaded"`.
- **Bind policy.** `127.0.0.1` only, hard-coded. Port from
  `YAYA_WEB_PORT` env (or constructor override for tests); `0`/unset
  asks the OS for a free port. Public bind is a GOAL.md non-goal
  through 1.0.
- **Session routing.** Each WS connection gets
  `session_id = f"ws-{uuid4().hex[:8]}"` at `accept()`. The adapter
  publishes `user.message.received` with that id; the agent loop
  propagates it through every downstream event; `_deliver()` routes
  by `ev.session_id` → `_clients[sid]`. Unmatched ids broadcast to
  all clients so lifecycle events (`kernel.ready`, `plugin.loaded`,
  ...) are visible on every browser tab.
- **Self-clean on disconnect** (lesson #6). The receive loop's
  `finally` removes the socket from its session set; when the set
  empties, the session entry is deleted. Idle sessions must not
  accumulate in a long-running process.
- **Static assets via `importlib.resources`.** The `static/`
  directory is git-tracked and shipped in the wheel; the server
  resolves it via `files("yaya.plugins.web") / "static"` so the
  path works in editable installs and installed wheels.
- **UI bundle.** The shipped `static/` directory is the output of
  `vite build` inside `src/yaya/plugins/web/` — a Vite-built
  integration of `@mariozechner/pi-web-ui@0.67.6` (MessageList,
  StreamingMessageContainer, Input, ConsoleBlock) plus the yaya
  WebSocket client and chat shell. End users still install via
  `pip` — Node is a contributor-only dependency. CI rebuilds
  `static/` and fails if the fresh output differs from the tracked
  bundle, keeping the wheel reproducible.
- **Framework ring boundary.** pi-web-ui modules that assume the
  browser owns the agent loop, API keys, or session storage are
  **forbidden** to preserve the Dependency Rule. Whitelist:
  `MessageList`, `StreamingMessageContainer`, `Input`,
  `ConsoleBlock`. Blacklist (pre-commit grep enforces): the
  upstream chat panel class, settings store and dialog,
  provider-keys store, sessions store, IndexedDB storage backend,
  upstream app storage, and every export from
  `@mariozechner/pi-agent-core`. See
  [`docs/wiki/lessons-learned.md`](../docs/wiki/lessons-learned.md)
  entry 27.
- **Uvicorn lifecycle.** Started via
  `asyncio.create_task(server.serve())` during `on_load`; stopped on
  `on_unload` via `server.should_exit = True` +
  `asyncio.wait_for(task, 3.0)`, cancelled on timeout.
- **Frame schema** is the short form documented in
  `docs/dev/web-ui.md`: `assistant.message.delta` → `assistant.delta`,
  `tool.call.start` → `tool.start`, and so on. Every frame carries
  `session_id` so the browser can scope streaming state to the
  right turn.

## Boundaries

### Allowed Changes
- src/yaya/plugins/web/__init__.py
- src/yaya/plugins/web/plugin.py
- src/yaya/plugins/web/AGENT.md
- src/yaya/plugins/web/static/
- src/yaya/plugins/web/src/
- src/yaya/plugins/web/package.json
- src/yaya/plugins/web/package-lock.json
- src/yaya/plugins/web/tsconfig.json
- src/yaya/plugins/web/vite.config.ts
- src/yaya/plugins/web/index.html
- tests/plugins/web/__init__.py
- tests/plugins/web/test_web_adapter.py
- tests/cli/test_serve.py
- specs/plugin-web.spec
- tests/bdd/features/plugin-web.feature
- tests/bdd/test_plugin_web.py
- pyproject.toml
- uv.lock

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/tool_bash/
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Web adapter exposes a WebSocket on 127.0.0.1
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_websocket_accepts_connection
  Level: integration
  Given a loaded web adapter plugin
  When a websocket client connects to the ws route
  Then the connection is accepted and bound to 127.0.0.1

Scenario: Browser user message round-trips as user.message.received
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_user_message_frame_emits_event
  Level: integration
  Given a loaded web adapter plugin with a websocket client connected
  When the client sends a user.message frame carrying text hi
  Then a user.message.received event is observed on the bus with text hi

Scenario: Assistant delta from the bus reaches the browser
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_assistant_delta_reaches_client
  Level: integration
  Given a loaded web adapter plugin with a websocket client connected on a session
  When an assistant.message.delta event is published for that session
  Then the client receives an assistant.delta frame with the same content

Scenario: Kernel-origin events broadcast to every connected client
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_kernel_event_broadcasts
  Level: integration
  Given a loaded web adapter plugin with two websocket clients connected
  When a kernel.ready event is published on the kernel session
  Then both clients receive the kernel.ready frame

Scenario: GET api plugins returns the adapter cached snapshot
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_api_plugins_snapshot
  Level: unit
  Given a loaded web adapter plugin that observed a plugin.loaded event
  When a client issues a GET request to api plugins
  Then the response body carries a plugins list with the observed row

Scenario: on_unload stops uvicorn within the timeout
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_on_unload_stops_server
  Level: integration
  Given a loaded web adapter plugin with an active uvicorn server
  When on_unload is awaited
  Then the uvicorn server task completes and clients are closed

Scenario: Shipped static bundle is a real Vite build
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_static_bundle_is_real_vite_build
  Level: unit
  Given the packaged web plugin static directory
  When its index.html is inspected
  Then it references Vite-hashed JS assets and no placeholder markers remain

## Out of Scope

- Authentication, authorization, and public-bind support — GOAL.md
  non-goals through 1.0.
- `yaya serve --dev` vite HMR proxy — flag is accepted and warns
  today; the actual proxy wiring lands with the pi-web-ui swap.
- `/api/plugins/install` and `/api/plugins/remove` proxies — the
  read-only snapshot is enough for 0.1; mutation endpoints come
  with the self-authoring milestone (0.5).
