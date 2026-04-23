spec: task
name: "kernel-agent-loop"
tags: [kernel, agent-loop, scheduler]
---

## Intent

The yaya kernel owns the agent loop — the scheduler that drives one
turn per user message with a fixed event ordering. Strategy plugins
decide content (which tool to call next, whether to query memory,
when to stop); the loop decides order. This contract pins down the
observable wire behavior before any real strategy, LLM provider,
tool, or memory plugin is built on top.

## Decisions

- `AgentLoop` lives in `src/yaya/kernel/loop.py` with
  `LoopConfig(max_iterations=16, step_timeout_s=60.0)` and drives one
  turn per `user.message.received` event through the bus.
- Event sequence per turn is frozen: strategy decide request/response,
  optional memory query/result, LLM call request/response, optional
  tool call start/request/result, then assistant.message.done. The
  happy path produces an assistant message; the tool round-trip emits
  tool.call.start and tool.call.result before assistant.message.done.
- Tool calls emitted by the loop carry `schema_version="v1"` so the
  kernel-side v1 dispatcher runs registered `Tool` subclasses. Legacy
  tools that subscribe directly to `tool.call.request` still receive
  the same event and may ignore the extra field.
- `tool.call.result` projection into the ReAct `Observation:` message
  handles both shapes: the v1 dispatcher's `{envelope: {brief, display}}`
  (TextBlock / MarkdownBlock surface as text; JsonBlock surfaces as
  `{brief, data}`) and the legacy `{value: ...}` shape emitted by
  `tool_bash`. Failures preserve `kind` and `brief` from the envelope
  so the LLM sees the tool-authored reason, not a generic "unknown".
- Correlation via event id: each outbound request event is matched
  with its response by echoing the request's id as
  `payload.request_id`; untracked responses carrying no matching
  request_id are ignored by the loop.
- Guard: `max_iterations` cap emits `kernel.error` with
  `message="max_iterations_exceeded"` and aborts the turn before
  `assistant.message.done`.
- Guard: a `user.interrupt` event for the active session aborts the
  current turn and suppresses further tool.call.request emissions for
  that turn.

## Boundaries

### Allowed Changes
- src/yaya/kernel/loop.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/events.py
- src/yaya/kernel/AGENT.md
- tests/kernel/test_loop.py
- tests/bdd/features/kernel-agent-loop.feature
- tests/bdd/test_kernel_agent_loop.py
- docs/dev/plugin-protocol.md
- specs/kernel-agent-loop.spec

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- src/yaya/kernel/bus.py
- src/yaya/kernel/plugin.py
- pyproject.toml
- GOAL.md

## Completion Criteria

Scenario: Happy path — user message drives AgentLoop to assistant.message.done
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_happy_path
  Level: unit
  Given an AgentLoop with a stub strategy that returns "llm" then "done"
  And a stub LLM provider returning "hello"
  When a user.message.received event is published
  Then an assistant.message.done event is observed with content "hello"
  And the frozen per-turn event sequence drove the turn

Scenario: Tool round-trip emits tool.call.start and tool.call.result before assistant.message.done
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_tool_roundtrip
  Level: unit
  Given an AgentLoop with a strategy that returns "tool" then "llm" then "done"
  And a stub tool plugin that echoes its args
  And a stub LLM provider
  When a user.message.received event arrives
  Then a tool.call.start event and a tool.call.result event are observed
  And they occur before assistant.message.done in the frozen event sequence
  And the tool.call.request payload uses schema_version v1

Scenario: Error path — max_iterations guard emits kernel.error and aborts the turn
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_max_iterations_guard
  Level: unit
  Given an AgentLoop configured with max_iterations=3
  And a strategy that never returns "done"
  When a user.message.received event arrives
  Then a kernel.error event is emitted carrying message "max_iterations_exceeded"
  And the turn aborts without assistant.message.done

Scenario: Error path — user.interrupt guard aborts the active turn for the session
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_interrupt_aborts_turn
  Level: unit
  Given an AgentLoop mid-turn awaiting a tool.call.result
  And the session worker is idle
  When a user.interrupt event is published for the same active session
  Then the current turn aborts under the interrupt guard
  And no further tool.call.request is emitted for that turn

Scenario: v1 tool envelope projects into the ReAct Observation
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_loop_projects_v1_envelope_into_llm_request
  Level: unit
  Given an AgentLoop with a registered v1 Tool returning a TextBlock envelope
  And a strategy that calls the tool then asks the LLM
  When a user.message.received event arrives
  Then the subsequent llm.call.request carries an Observation message with the tool's text

Scenario: Correlation via request_id — untracked response without matching request_id is ignored
  Test:
    Package: yaya
    Filter: tests/kernel/test_loop.py::test_untracked_response_is_ignored
  Level: unit
  Given an AgentLoop with an in-flight outbound request tracked by its event id
  When a response event arrives carrying no matching request_id correlation
  Then the untracked response is ignored and the loop keeps awaiting the correlated reply

## Out of Scope

- Real strategy, LLM provider, tool, or memory plugin implementations
  (stubs only at this layer).
- New public event kinds (the per-turn sequence uses the frozen
  catalog from `docs/dev/plugin-protocol.md`).
