spec: task
name: "plugin-strategy_react"
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
- Implements classical ReAct (Yao et al. 2022): the strategy injects a
  system prompt via `messages_prepend` that constrains the LLM to emit
  either a `Thought: ... Action: <tool> Action Input: <json>` triple or
  a `Thought: ... Final Answer: <text>` termination. Tool intent rides
  in free-form assistant text; the strategy does not consume
  `assistant.tool_calls`.
- Accepts provider-style `[TOOL_CALL]` JSON blocks as a compatibility
  input for tool intent. A block shaped as `{"tool": <name>,
  "tool_input": <object>}` maps to the same `tool_call` decision as a
  ReAct Action, and takes precedence over a nearby `Final Answer`
  marker so mixed responses do not silently terminate before the tool
  runs.
- When the most-recent assistant message parses to a valid Action, the
  decision is `{"next": "tool", "tool_call": {"id", "name", "args"}}`
  with a synthesized id; only the first Action per turn is surfaced.
- When the most-recent assistant message parses to a Final Answer the
  decision is `{"next": "done"}`.
- When no assistant message has been produced yet on the turn, the
  decision is `{"next": "llm", "provider": <configured>, "model":
  <configured>, "messages_prepend": [<ReAct system prompt>]}`.
- After any message lands on top of the last assistant message (an
  Observation appended by the loop, for example), the decision loops
  back to `{"next": "llm", ...}` so the LLM can read the observation
  and continue the turn.
- When the assistant message fails to parse into either shape, the
  strategy appends one corrective `role="user"` nudge (marked with
  `[yaya:react-format-nudge] `) via `messages_append` and re-rolls.
  A second consecutive parse failure terminates the turn with
  `{"next": "done"}` — no endless retries.
- When `mercari_jp_search` is present in the tool registry, the system
  prompt appends a Shopping Output Contract pinning the Final Answer
  to a single markdown table (`| Rank | Title | Price (JPY) |
  Condition | Why it fits | Link |`, exactly 3 rows), requiring each
  `Why it fits` to cite a user-stated constraint. Absent the tool the
  contract is omitted so generic chats are unaffected.

## Boundaries

### Allowed Changes
- src/yaya/plugins/strategy_react/__init__.py
- src/yaya/plugins/strategy_react/plugin.py
- src/yaya/plugins/strategy_react/AGENT.md
- tests/plugins/strategy_react/__init__.py
- tests/plugins/strategy_react/test_strategy_react.py
- tests/bdd/features/plugin-strategy_react.feature
- tests/bdd/test_plugins.py
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

Scenario: Assistant message with a well-formed Action decides next step is tool with the parsed tool_call
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_assistant_with_react_action_returns_tool
  Level: unit
  Given a strategy.decide.request whose last assistant message contains a ReAct Action and Action Input
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next tool and the parsed tool_call payload
  And the response echoes the originating request id

Scenario: Assistant message with a bracketed tool-call block decides next step is tool
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_assistant_with_tool_call_block_returns_tool
  Level: unit
  Given a strategy.decide.request whose last assistant message contains Final Answer prose and a [TOOL_CALL] block
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next tool and the parsed tool_call payload
  And the Final Answer prose does not terminate the turn before the tool runs
  And the response echoes the originating request id

Scenario: Observation follows the last assistant message so the next step loops back to llm
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_post_observation_returns_llm
  Level: unit
  Given a strategy.decide.request whose state has an Observation user message after the last assistant message
  When the ReAct plugin handles the event
  Then a strategy.decide.response is emitted with next llm and the configured provider and model
  And the response echoes the originating request id

Scenario: Assistant message with a Final Answer label decides next step is done
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_assistant_with_final_answer_returns_done
  Level: unit
  Given a strategy.decide.request whose last assistant message contains a Final Answer label
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

Scenario: Shopping output contract appears only when mercari_jp_search is registered
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_system_prompt_adds_shopping_contract_when_mercari_tool_present
  Level: unit
  Given the live tool registry contains mercari_jp_search
  When the ReAct system prompt is composed
  Then it pins the Final Answer to the required 3-row markdown table shape

## Out of Scope

- Memory step emission (seed ReAct at 0.1 does not call `memory.*`).
- Configuration loading via `ctx.config` beyond the seed defaults.
- Multi-tool parallel dispatch within one turn.
