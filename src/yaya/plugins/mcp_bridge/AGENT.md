## Philosophy
MCP bridge plugin. Spawns external MCP servers configured under `[mcp_bridge.servers.<name>]`, discovers their tools over the standard MCP stdio handshake, and registers each as a native yaya `Tool`. The bridge is the single place where untrusted external tool surfaces enter yaya — every derived tool defaults to `requires_approval=True`.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (Tool row + `x.<plugin>.<kind>` namespace).
- Contract: [`specs/plugin-mcp_bridge.spec`](../../../../specs/plugin-mcp_bridge.spec); tests under `tests/plugins/mcp_bridge/`.
- MCP stdio wire format: <https://spec.modelcontextprotocol.io/> (`2024-11-05`).

## Constraints
- `Category.TOOL`. Subscribes to **nothing**: dispatch goes through the kernel's v1 tool dispatcher (`yaya.kernel.tool.dispatch`) over the registry populated in `on_load`.
- **Vendored client.** No `mcp` / `fastmcp` dependency — MCP stdio is newline-delimited JSON-RPC 2.0; a ~200-line client lives in `client.py` per root AGENT.md §4 ("vendor a minimal implementation"). The upstream SDKs would drag a large pydantic + HTTP graph we do not need at 0.1.
- **Subprocess lifecycle (lesson #31).** Every teardown follows `terminate()` → `wait_for(proc.wait(), grace_s)` → `kill()` fallback. No bare `proc.wait()` without a deadline.
- **Boot retries.** Three attempts, 0.5/1.0/2.0 s backoff (override via `MCPBridge(retry_delays_s=...)` in tests). On exhaustion: emit `x.mcp.server.error`, keep the rest of the bridge running.
- **Approval gate (issue #31 hard rule).** `requires_approval=True` is the default for every MCP-derived tool. Per-server `requires_approval = false` exists for trusted operators but is **never** flipped without an explicit config opt-in.
- **Error translation (lesson #29).** `MCPClientError` / `OSError` / `TimeoutError` route through `tool.run` and translate to `ToolError(kind=...)`; no exception escapes the dispatcher.
- **Extension events.** `x.mcp.server.ready` / `x.mcp.server.error` are plugin-private (GOAL.md #3) — bus-routed, not in the closed public catalog.

## Interaction
Drop a table into `~/.config/yaya/config.toml` and restart `yaya serve`; tools appear as `mcp_<server>_<tool>`:

```toml
# filesystem: no env needed
[mcp_bridge.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
env = {}
enabled = true
requires_approval = true  # default — file access is sensitive
call_timeout_s = 30.0

[mcp_bridge.servers.github]
command = "uvx"
args = ["mcp-server-github"]
env = { GITHUB_TOKEN = "$GITHUB_TOKEN" }  # expanded from process env
```

`$VAR` / `${VAR}` in `command`, `args`, and `env` values expands via `os.path.expandvars` at boot. Hot reload is not supported at 0.1 — restart after a config change.

## Testing knobs
`MCPBridge(retry_delays_s=(0.0,))` collapses retries; `MCPBridge(client_factory=...)` injects a fake honouring `start` / `call_tool` / `close`. The integration test ships a pure-Python stdio MCP server at `tests/plugins/mcp_bridge/_fake_server.py` — no network, no `npx` / `uvx` subprocess.

## Budget & Loading
Sibling: [`../AGENT.md`](../AGENT.md). Lessons honoured: #15 (`request_id` echo), #29 (exception translation in `tool.run`), #31 (subprocess cancel in `client.close`).
