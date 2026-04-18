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

## 0.1 preview UI
The `static/` directory ships a minimal hand-written HTML/JS/CSS shell (~200 LOC vanilla JS) so `pip install yaya && yaya serve` works without Node. A full `@mariozechner/pi-web-ui`-based replacement is a future PR; the WS schema is stable regardless.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#plugin-categories-closed-set) + [`docs/dev/web-ui.md`](../../../../docs/dev/web-ui.md).
- Lessons that apply: #1 (no asyncio.Lock), #6 (self-clean dicts), #10 (silent drops log WARNING), #15 (log on unexpected drops), #21 (tight pragma scope).
