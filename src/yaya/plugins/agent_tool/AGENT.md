# agent-tool

Bundled `tool` plugin (issue #34). Exposes one v1 tool, `agent`, that
spawns a sub-agent by forking the caller's
`yaya.kernel.session.Session` and pumping the child turn through the
same kernel `AgentLoop` the parent uses.

## Surface

- Tool name: `agent`.
- Params (pydantic): `goal: str`, `strategy: str = "react"`,
  `tools: list[str] | None`, `max_steps: int = 20`,
  `max_wall_seconds: float = 300.0`.
- `requires_approval = True` — every spawn flows through the
  approval runtime (#28).

## Fork semantics

`AgentTool.run` calls `parent.fork(child_id)` — the overlay tape from
#32 gives the child parent-readable history plus its own private
appends. Child writes never mutate the parent. The child session id is
`<parent>::agent::<uuid8>`; the `::agent::` separator doubles as the
depth counter (`_depth_of`).

## Guards

- **Depth**: `max_depth = 5` (overridable via `[agent_tool] max_depth`
  in TOML). Depth ≥ cap → `ToolError(kind="rejected")` before any
  fork.
- **Approval**: inherited from the base `Tool.pre_approve` — rejection
  surfaces as `ToolError(kind="rejected")` per the v1 contract.
- **Timeout**: `max_wall_seconds` → `ToolError(kind="timeout")`.
- **Cancellation**: parent turn cancel propagates via
  `asyncio.CancelledError`; the tool emits
  `x.agent.subagent.failed(reason="cancelled")` in `finally`.

## Events

Plugin-private (`x.agent.*` extension namespace, routed on a stable
bridge session `_bridge:agent-tool` per lesson #2):

- `x.agent.subagent.started(parent_id, child_id, goal, strategy, tools)`
- `x.agent.subagent.completed(child_id, final_text, steps_used, forbidden_tool_hits)`
- `x.agent.subagent.failed(child_id, reason)`
- `x.agent.allowlist.narrowed(child_id, attempted, allowed)`

## Tests

- Unit: `tests/plugins/agent_tool/test_agent_tool.py`
- Spec: `specs/plugin-agent_tool.spec`
- Mirror: `tests/bdd/features/plugin-agent_tool.feature`
