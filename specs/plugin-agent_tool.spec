spec: task
name: "plugin-agent_tool"
tags: [plugin, tool, multi-agent]
---

## Intent

The agent tool plugin exposes one v1 tool, `agent`, that spawns a
sub-agent by forking the caller's `Session` (via the overlay store
from #32) and driving the child turn through the same kernel
`AgentLoop` the parent uses. The final `assistant.message.done` on
the child session resolves the tool's return envelope.

## Decisions

- Sub-agents are `tool` category, not a new plugin category. The
  kernel stays small (GOAL.md principle #1).
- `requires_approval = True` is mandatory; spawning a sub-agent
  amplifies capability and may burn tokens arbitrarily.
- Child session id encodes depth: `<parent>::agent::<uuid8>`. The
  `::agent::` separator doubles as the depth counter; a root session
  has depth 0. Depth `≥ max_depth` refuses the spawn with
  `ToolError(kind="rejected")` before any fork.
- The plugin caches `(session, bus)` during `on_load`; the v1
  dispatcher does not thread a session into its `KernelContext`, and
  threading session through every `tool.call.request` payload would
  pollute the closed public catalog (principle #3).
- Parent tape immutability is delegated to
  `Session.fork`'s `_ForkOverlayStore`: child appends never mutate
  the parent's tape.
- Tool filtering is observational at 0.2: every
  `tool.call.request` the child emits is compared against the
  allowlist; offenders are recorded and surfaced via
  `x.agent.allowlist.narrowed`. Hard enforcement requires kernel
  changes outside the 0.2 scope. `tools=None` inherits all parent
  tools; `tools=[]` treats every call as forbidden (records all as
  narrowed).
- Progress events live under the `x.agent.*` extension namespace
  (principle #3) on a stable bridge session
  `_bridge:agent-tool` so they never interleave with conversation
  FIFOs (lesson #2).
- Timeout is the sub-agent's wall-clock deadline
  (`max_wall_seconds`, default 300s). Exhaustion surfaces as
  `ToolError(kind="timeout")` and emits `x.agent.subagent.failed(reason="timeout")`.
- Cancellation via `asyncio.CancelledError` propagates out of
  `AgentTool.run`; `finally` emits `x.agent.subagent.failed(reason="cancelled")`
  before the unwind completes.

## Boundaries

### Allowed Changes
- src/yaya/plugins/agent_tool/__init__.py
- src/yaya/plugins/agent_tool/plugin.py
- src/yaya/plugins/agent_tool/AGENT.md
- tests/plugins/agent_tool/__init__.py
- tests/plugins/agent_tool/test_agent_tool.py
- tests/plugins/agent_tool/_fake_strategy.py
- tests/bdd/features/plugin-agent_tool.feature
- specs/plugin-agent_tool.spec
- pyproject.toml
- docs/dev/plugin-protocol.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- GOAL.md
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/llm_echo/
- src/yaya/plugins/tool_bash/
- src/yaya/plugins/mcp_bridge/
- src/yaya/plugins/web/

## Completion Criteria

Scenario: Happy path — forked sub-agent returns its final text as a ToolOk
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_happy_path_subagent_returns_final_text
  Level: integration
  Given a loaded agent-tool plugin with a fake agent loop answering "42"
  When a tool.call.request for name agent with goal is published
  Then a tool.call.result event is emitted with ok true and final text 42
  And the child session id is prefixed with parent::agent::

Scenario: Parent tape stays immutable after the child runs
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_parent_tape_is_immutable_after_child_runs
  Level: integration
  Given a loaded agent-tool plugin and a baseline parent tape length
  When a sub-agent completes one turn on the forked child session
  Then the parent tape length is unchanged

Scenario: Depth guard blocks runaway recursion before any fork
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_depth_guard_blocks_runaway_recursion
  Level: unit
  Given a parent session whose id already carries max_depth hops
  When a tool.call.request for agent is dispatched on that session
  Then a tool.call.result with ok false and kind rejected mentioning max depth is emitted

Scenario: Timeout surfaces as ToolError kind timeout
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_timeout_returns_tool_error
  Level: integration
  Given a loaded agent-tool plugin and a fake loop that never finishes
  When a sub-agent is spawned with a sub-second max_wall_seconds
  Then a tool.call.result with ok false and kind timeout is emitted
  And x.agent.subagent.failed with reason timeout is emitted

Scenario: Tool allowlist records forbidden hits and narrows via extension event
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_allowlist_records_forbidden_hits
  Level: integration
  Given a loaded agent-tool plugin and a fake loop that issues a forbidden tool call
  When a sub-agent is spawned with a non-empty tools allowlist that excludes the tool
  Then x.agent.allowlist.narrowed is emitted listing the attempted and allowed tool names
  And x.agent.subagent.completed records the forbidden hit

Scenario: Cancellation during run emits a failed event with reason cancelled
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_cancellation_emits_failed_event
  Level: integration
  Given a running AgentTool awaiting a sub-agent that will never finish
  When the running task is cancelled
  Then x.agent.subagent.failed with reason cancelled is emitted

Scenario: The agent tool requires approval by default
  Test:
    Package: yaya
    Filter: tests/plugins/agent_tool/test_agent_tool.py::test_agent_tool_requires_approval_by_default
  Level: unit
  Given the AgentTool class
  When its requires_approval flag is read
  Then requires_approval is true and name equals agent

## Out of Scope

- Hard kernel-side enforcement of the tool allowlist (requires a
  dispatcher hook beyond 0.2 scope).
- Parallel sub-agents and cross-session aggregation; one spawn per
  call at 0.2.
- Agent type registry with per-type system prompts — the 0.2 build
  ships one reference flow where the strategy is chosen by the
  running kernel.
