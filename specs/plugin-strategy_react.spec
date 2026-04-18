spec: task
name: "plugin-strategy_react"
inherits: project
tags: [plugin, strategy]
---

## Intent

The ReAct strategy plugin answers `strategy.decide.request` with the
next step the kernel's fixed agent loop should take. It inspects the
loop state snapshot — most-recent assistant message, pending
tool_calls, last tool result — and picks one of `llm`, `tool`, or
`done`. The plugin carries no per-session state; every decision is
pure over the request payload, which keeps the seed strategy trivially
testable and deterministic.

## Decisions

- Subscribes only to `strategy.decide.request` and emits exactly one
  `strategy.decide.response` per request, echoing the originating
  event's `id` back as `request_id` so the loop's `_RequestTracker`
  can correlate.
- When the most-recent assistant message carries a non-empty
  `tool_calls` list, the decision is `{"next": "tool", "tool_call":
  <first>}`; the kernel runs tool calls sequentially, so only the
  first is surfaced per turn.
- When no assistant message has been produced yet on the turn, the
  decision is `{"next": "llm", "provider": <configured>, "model":
  <configured>}` using the hardcoded `openai` / `gpt-4o-mini`
  defaults — `ctx.config` is consulted first for when registry P3
  plumbs per-plugin config.
- When the most-recent assistant message exists and has no pending
  tool_calls AND no unconsumed tool result, the turn ends with
  `{"next": "done"}`.
- After a tool result lands, the decision loops back to
  `{"next": "llm", ...}` so the LLM can read the tool output and
  continue the turn.

## Boundaries

### Allowed Changes
- src/yaya/plugins/strategy_react/__init__.py
- src/yaya/plugins/strategy_react/plugin.py
- src/yaya/plugins/strategy_react/AGENT.md
- tests/plugins/strategy_react/__init__.py
- tests/plugins/strategy_react/test_strategy_react.py
- specs/plugin-strategy_react.spec

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/tool_bash/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: No assistant message yet decides next step is llm with configured provider and model
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_no_assistant_yet_returns_llm
  Level: unit
  Given a strategy.decide.request whose state has no prior assistant message
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next llm and the configured provider and model
  And the response echoes the originating request id

Scenario: Pending tool_calls on the assistant message decide next step is tool with first tool call
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_assistant_with_tool_calls_returns_tool
  Level: unit
  Given a strategy.decide.request whose last assistant message carries a non-empty tool_calls list
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next tool and the first pending tool_call payload
  And the response echoes the originating request id

Scenario: Tool result just landed decides next step loops back to llm for another pass
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_after_tool_result_returns_llm
  Level: unit
  Given a strategy.decide.request whose last_tool_result is populated after an assistant step
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next llm and the configured provider and model
  And the response echoes the originating request id

Scenario: Assistant message without tool_calls or pending tool result decides next step is done
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_assistant_without_tool_calls_returns_done
  Level: unit
  Given a strategy.decide.request whose last assistant message has no tool_calls and no pending tool result
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next done
  And the response echoes the originating request id

Scenario: Error path — strategy.decide.request missing state payload raises for plugin.error synthesis
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_missing_state_raises
  Level: unit
  Given a strategy.decide.request whose payload omits the state key entirely
  When the ReAct plugin handles the event
  Then the handler raises ValueError so the kernel synthesizes a plugin.error

## Out of Scope

- Memory step emission (seed ReAct at 0.1 does not call `memory.*`).
- Configuration loading via `ctx.config` beyond the seed defaults.
- Multi-tool parallel dispatch within one turn.
