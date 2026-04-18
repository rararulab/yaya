## Philosophy
Argv-only bash tool plugin. Never `shell=True`. Runs a `list[str]` through `asyncio.create_subprocess_exec`, enforces a 30 s wall-clock, and emits `tool.call.result` with stdout / stderr / returncode.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (Tool row).
- Contract: [`specs/plugin-tool_bash.spec`](../../../../specs/plugin-tool_bash.spec).
- Tests: `tests/plugins/tool_bash/`.

## Constraints
- `Category.TOOL`. Subscribes only to `tool.call.request`; filters by `payload["name"] == "bash"`.
- **Never `shell=True`** — the project-wide ban from the plugins-folder AGENT.md. argv list only. `payload.args.cmd` must be `list[str]`; any other shape → `{"ok": False, "error": "cmd must be argv list (list of strings)"}`.
- Default 30 s timeout via `asyncio.wait_for`. Overrun → `proc.kill()` + `await proc.wait()` + `{"ok": False, "error": "timeout"}`. Configurable via `BashTool(timeout_s=...)` for tests.
- Every `tool.call.result` echoes `request_id` (lesson #15). Success carries `id` (logical call id) + `value` (`stdout` / `stderr` / `returncode`).
- No third-party AI agent frameworks (AGENT.md §4). Stdlib + `yaya.kernel.*` only.

## Interaction (patterns)
- Request filter: `ev.payload.get("name") != "bash"` → return silently so siblings like a future `tool-fs` can coexist on the shared subscription.
- `on_load`/`on_unload`: no-ops (stateless). `on_load` emits a DEBUG line for boot traceability.
- Do NOT buffer stdout/stderr in memory beyond one subprocess lifetime; `proc.communicate()` already returns full byte strings at exit.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#tool-execution-kernel--tool).
