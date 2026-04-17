# kernel-agent-loop

## Intent

The yaya kernel owns the agent loop — the scheduler that drives one turn per
user message with a fixed event ordering. Strategy plugins decide content
(which tool to call next, whether to query memory, when to stop); the loop
decides order. This contract pins down the observable wire behavior before
any real strategy, LLM provider, tool, or memory plugin is built on top.

## Decisions

- Loop lives in `src/yaya/kernel/loop.py` as `AgentLoop` with
  `@dataclass(slots=True) LoopConfig(max_iterations=16, step_timeout_s=60.0)`.
- The loop depends only on `yaya.kernel.bus`, `yaya.kernel.events`, and
  `yaya.kernel.plugin`. No imports from `cli`, `plugins`, or `core`. No
  import of any concrete plugin implementation — the loop communicates
  with strategies, LLM providers, tools, and memory plugins exclusively
  through the bus.
- Event sequence is frozen to
  `docs/dev/plugin-protocol.md#agent-loop-kernel-owned`:
  `user.message.received → strategy.decide.request → strategy.decide.response
  → (memory.query → memory.result)* → llm.call.request → llm.call.response
  → (tool.call.start → tool.call.request → tool.call.result)* →
  assistant.message.done → memory.write?`.
- **Correlation via event id.** Each outbound request (`strategy.decide.request`,
  `memory.query`, `llm.call.request`, `tool.call.request`) is correlated
  with its response by the originating event's `id`. Plugins MUST echo
  the request's `id` as `payload.request_id` on the corresponding
  response event. A private `_RequestTracker` resolves awaiters by
  `request_id`; untracked responses are ignored. This is an **additive**
  field on five existing response payloads — not a governance change.
- Per-turn tasks are spawned with an **empty** `contextvars.Context()` so
  the bus's private `_IN_WORKER` ContextVar resets to its default; this
  keeps the loop's `publish` calls awaiting delivery (rather than
  fire-and-forget) for correct step-by-step progression.
- Guards: `max_iterations` cap trips `kernel.error` with
  `source="agent_loop"` and `message="max_iterations_exceeded"`, aborting
  the turn before `assistant.message.done`. `user.interrupt` cancels the
  active turn for that session.

## Boundaries

- **Allowed**:
  - `src/yaya/kernel/loop.py`
  - `src/yaya/kernel/__init__.py` (re-export `AgentLoop`, `LoopConfig`)
  - `src/yaya/kernel/events.py` (additive `request_id` on 5 response
    payloads — non-governance)
  - `src/yaya/kernel/AGENT.md` (loop constraint bullet)
  - `tests/kernel/test_loop.py`
  - `docs/dev/plugin-protocol.md` (request_id columns + correlation note)
  - `specs/kernel-agent-loop.spec.md`
- **Forbidden**: everywhere else. No new public event kinds. No new
  dependencies. No edits to `cli/`, `plugins/`, `core/`, `GOAL.md`,
  `pyproject.toml`, or `bus.py` / `plugin.py`.

## Completion Criteria (BDD)

Scenario: Happy path — user message produces assistant message
  Given an AgentLoop with a stub strategy returning "llm" then "done"
  And a stub LLM provider returning "hello"
  When a user.message.received event is published
  Then an assistant.message.done event is observed with content "hello"
  Test: tests/kernel/test_loop.py::test_happy_path

Scenario: Tool call round-trip
  Given an AgentLoop with a strategy that returns "tool" then "llm" then "done"
  And a stub tool plugin that echoes its args
  And a stub LLM provider
  When a user.message.received event arrives
  Then tool.call.start and tool.call.result are observed before assistant.message.done
  Test: tests/kernel/test_loop.py::test_tool_roundtrip

Scenario: Max-iterations guard
  Given an AgentLoop with max_iterations=3 and a strategy that never returns "done"
  When a user.message.received event arrives
  Then a kernel.error with message "max_iterations_exceeded" is emitted
  And the turn ends without assistant.message.done
  Test: tests/kernel/test_loop.py::test_max_iterations_guard

Scenario: Interrupt ends the turn cleanly (idle-worker precondition)
  Given an AgentLoop mid-turn awaiting a tool.call.result
  And the session worker is idle (no handler currently executing)
  When a user.interrupt event is published for the same session
  Then the current turn aborts
  And no further tool.call.request is emitted for that turn
  Test: tests/kernel/test_loop.py::test_interrupt_aborts_turn
