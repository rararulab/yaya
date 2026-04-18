spec: task
name: "plugin-mcp_bridge"
tags: [plugin, tool, mcp]
---

## Intent

The MCP bridge plugin loads external Model Context Protocol servers
configured under `[mcp_bridge.servers.<name>]` and registers each
server-advertised tool as a native yaya `Tool`. Each derived tool
defaults to `requires_approval=True` because MCP servers are
external untrusted code surfaces (issue #31 hard rule). The bridge
owns the subprocess lifecycle for every spawned server: boot uses
3 retries with exponential backoff, teardown follows the lesson #31
terminate-then-kill pattern, and per-server failures emit the
plugin-private `x.mcp.server.error` event without tainting the rest
of the bridge.

## Decisions

- Subscribes to no public events. Inbound `tool.call.request` for
  MCP-derived tools flow through the kernel's v1 dispatcher
  (`yaya.kernel.tool.dispatch`), which looks the tool up in the
  registry populated during `on_load` via
  `yaya.kernel.tool.register_tool`.
- Each MCP-derived tool's yaya-side name is
  `mcp_<server>_<tool>` so collisions across servers are
  impossible and the LLM function-calling surface receives a
  stable identifier.
- The MCP stdio wire format is implemented inline as a vendored
  newline-delimited JSON-RPC 2.0 client; no `mcp` or `fastmcp`
  dependency. Per AGENT.md §4, MCP is a protocol not an agent
  framework, but vendoring keeps the dependency footprint flat.
- Boot retries use a fixed 0.5 / 1.0 / 2.0 second exponential
  backoff. After three exhausted attempts the server is given up
  on, the bridge emits `x.mcp.server.error` with
  `kind="boot_failed"`, and the rest of the bridge keeps running.
- Subprocess teardown in `MCPClient.close` runs `terminate()`
  first, awaits exit under a bounded `wait_for(proc.wait(),
  grace_s)`, and falls back to `kill()` if the child does not
  cooperate (lesson #31).
- Approval default is True for every derived tool; a per-server
  `requires_approval = false` config override exists for trusted
  operators but is never the default.
- Tool exception paths route through `Tool.run` and translate
  every `MCPClientError` / timeout / crash to a `ToolError`
  envelope with a kind chosen from `timeout`, `crashed`,
  `internal` (lesson #29 — no raw exception reaches the
  dispatcher).

## Boundaries

### Allowed Changes
- src/yaya/plugins/mcp_bridge/__init__.py
- src/yaya/plugins/mcp_bridge/plugin.py
- src/yaya/plugins/mcp_bridge/client.py
- src/yaya/plugins/mcp_bridge/config.py
- src/yaya/plugins/mcp_bridge/tool_factory.py
- src/yaya/plugins/mcp_bridge/AGENT.md
- tests/plugins/mcp_bridge/__init__.py
- tests/plugins/mcp_bridge/_fake_server.py
- tests/plugins/mcp_bridge/test_mcp_bridge.py
- tests/bdd/features/plugin-mcp_bridge.feature
- specs/plugin-mcp_bridge.spec
- pyproject.toml
- docs/dev/plugin-protocol.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/tool_bash/
- GOAL.md

## Completion Criteria

Scenario: Discovers tools from a fake MCP server and registers them by qualified name
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_discovers_and_registers_tools
  Level: integration
  Given a fake MCP server exposing one tool echo with a string parameter
  When the bridge loads with that server configured
  Then the kernel tool registry contains a tool named mcp_local_echo
  And an x.mcp.server.ready event is emitted naming the server and its tools

Scenario: Approval default is True for every MCP derived tool
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_derived_tool_requires_approval_by_default
  Level: unit
  Given a fake MCP server descriptor for a tool named echo
  When the tool factory builds a yaya Tool subclass with the bridge default
  Then the resulting class requires approval

Scenario: Tool call forwards arguments and wraps the response in ToolOk
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_tool_call_forwards_and_wraps_ok
  Level: integration
  Given a loaded bridge with a fake MCP server exposing echo
  When the echo tool is invoked through the kernel dispatcher with an approved gate
  Then the dispatcher emits one tool.call.result with ok true
  And the envelope brief reflects the echoed text

Scenario: Boot failure exhausts retries and emits x mcp server error
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_boot_failure_emits_server_error_after_retries
  Level: integration
  Given a fake MCP server factory that always fails to start
  When the bridge loads with retries collapsed
  Then no tools are registered for the failing server
  And exactly one x.mcp.server.error event is emitted with kind boot_failed

Scenario: Bad config entry surfaces as x mcp server error without tainting siblings
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_bad_config_emits_error_and_does_not_taint_others
  Level: unit
  Given a configuration mixing one valid server and one server whose command field is empty
  When the bridge loads
  Then an x.mcp.server.error event is emitted naming the bad server
  And the valid server still registers its tool

Scenario: Unload closes every spawned client deterministically
  Test:
    Package: yaya
    Filter: tests/plugins/mcp_bridge/test_mcp_bridge.py::test_unload_closes_every_client
  Level: integration
  Given a loaded bridge with two fake MCP servers
  When on_unload runs
  Then close has been awaited on both client instances

## Out of Scope

- SSE / HTTP transport for remote MCP servers (stdio only at 0.1).
- Hot reload of the bridge after editing `~/.config/yaya/config.toml` —
  restart `yaya serve` for now.
- Per-tool rate limiting and quota tracking.
- A dedicated `yaya mcp` CLI subcommand: GOAL.md restricts kernel
  built-ins; the bridge surfaces normally through `yaya plugin list`.
