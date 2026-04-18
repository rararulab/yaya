## Philosophy
MCP bridge plugin. Spawns external MCP servers configured under `[mcp_bridge.servers.<name>]`, discovers their tools over the standard MCP stdio handshake, and registers each as a native yaya `Tool`. The bridge is the single place where untrusted external tool surfaces enter yaya — every derived tool defaults to `requires_approval=True`.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (Tool row + `x.<plugin>.<kind>` extension namespace).
- Contract: [`specs/plugin-mcp_bridge.spec`](../../../../specs/plugin-mcp_bridge.spec).
- Tests: `tests/plugins/mcp_bridge/`.
- MCP protocol reference: <https://spec.modelcontextprotocol.io/>. We speak the `2024-11-05` revision of the stdio transport.

## Constraints
- `Category.TOOL`. Subscribes to **nothing**: dispatch happens through the kernel's v1 tool dispatcher (`yaya.kernel.tool.dispatch`) using the registry populated in `on_load`.
- **Vendored client.** No `mcp` / `fastmcp` dependency — the stdio MCP wire format is newline-delimited JSON-RPC 2.0; a 200-line client lives in `client.py` per AGENT.md §4 ("vendor a minimal implementation"). MCP itself is a *protocol*, not an agent framework, but pulling in the upstream SDKs would drag in a large pydantic + HTTP graph we do not need at 0.1.
- **Subprocess lifecycle (lesson #31).** Every client teardown follows `terminate()` → `wait_for(proc.wait(), grace_s)` → `kill()` fallback. No bare `proc.wait()` without a deadline.
- **Boot retries.** Three attempts with 0.5/1.0/2.0 s exponential backoff (configurable via `MCPBridge(retry_delays_s=...)` for tests). On exhaustion the bridge emits `x.mcp.server.error` and keeps the rest of the bridge running — fail loud, degrade gracefully.
- **Approval gate (issue #31 hard rule).** `requires_approval=True` is the default for every MCP-derived tool. A per-server `requires_approval = false` override exists for trusted operators but the default is **never** flipped without an explicit config opt-in.
- **Pre-approve translation (lesson #29).** All `MCPClientError` / `OSError` / `TimeoutError` paths route through `tool.run` and translate to `ToolError(kind=...)`; no exception escapes the dispatcher.
- **Extension events.** `x.mcp.server.ready` / `x.mcp.server.error` are plugin-private per GOAL.md principle #3 — they route through the bus but are NOT in the closed public catalog.

## Interaction (patterns)
- **Add a server.** Drop a `[mcp_bridge.servers.<name>]` table in `~/.config/yaya/config.toml` with `command`, `args`, optional `env`, `enabled`. Restart `yaya serve`; tools appear as `mcp_<server>_<tool>`.
- **Env expansion.** `$VAR` / `${VAR}` in `command`, `args`, and `env` values is expanded via `os.path.expandvars` against the process env at boot.
- **Hot reload.** Not supported at 0.1 — restart `yaya serve` after a config change. Tracked under the wider plugin hot-reload story.

## Testing knobs
`MCPBridge(retry_delays_s=(0.0,))` collapses retries for fast tests. `MCPBridge(client_factory=...)` lets a test inject a fake client honouring the `start` / `call_tool` / `close` surface. The integration test ships a tiny pure-Python stdio MCP server under `tests/plugins/mcp_bridge/_fake_server.py` — no network, no subprocess of `npx` or `uvx`.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative protocol: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#tool-execution-kernel--tool).
- Lessons honoured: #15 (`request_id` echo via the dispatcher), #29 (exception translation in `tool.run`), #31 (subprocess cancel pattern in `client.close`).
