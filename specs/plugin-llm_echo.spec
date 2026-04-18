spec: task
name: "plugin-llm_echo"
tags: [plugin, llm-provider, dev]
---

## Intent

The echo LLM-provider plugin is a deterministic, zero-config dev
provider that lets a fresh `yaya serve` round-trip the kernel
end-to-end without any API key. It subscribes to `llm.call.request`,
filters by the `echo` provider id so sibling providers can coexist,
and replies with `(echo) <last user message>` at zero token usage.

The plugin closes the 0.1 onboarding gap (`GOAL.md` §Milestones 0.1):
`pip install yaya && yaya serve` opens the browser, the user types
"hello", and `(echo) hello` comes back through the full
kernel→strategy→llm→assistant flow. Auto-selection is implemented
in `strategy_react`: when no `OPENAI_API_KEY` is present, the
strategy picks `provider == "echo"` (temporary env sniff; migrates
to `ctx.config` in #23).

## Decisions

- Subscribes only to `llm.call.request`; handler returns silently
  when `payload["provider"]` is not `"echo"` so other provider
  plugins own their own traffic on the same subscription.
- Response body is `f"(echo) {last_user_message}"` over the most
  recent `role == "user"` content in `messages`. When no user
  message is present (or its content is empty), the response is the
  literal `(echo) (no input)`.
- `usage` is hard-coded to `{"input_tokens": 0, "output_tokens": 0}`
  — this is a dev provider, not a real LLM call.
- `tool_calls` is always `[]`. The echo provider never asks for
  tool execution.
- Every emitted `llm.call.response` echoes `request_id` (per the
  lessons-learned correlation rule) so the agent loop's
  `_RequestTracker` correlates concurrent in-flight calls.
- Stdlib only. No third-party AI agent frameworks (`AGENT.md` §4)
  and no LLM SDK. `on_unload` is a no-op.
- Strategy fallback (in `strategy_react`): when `OPENAI_API_KEY`
  is unset, `_provider_and_model` returns `("echo", "echo")` so
  the bundled echo plugin answers the request.

## Boundaries

### Allowed Changes
- src/yaya/plugins/llm_echo/__init__.py
- src/yaya/plugins/llm_echo/plugin.py
- src/yaya/plugins/llm_echo/AGENT.md
- src/yaya/plugins/strategy_react/plugin.py
- tests/plugins/llm_echo/__init__.py
- tests/plugins/llm_echo/test_echo.py
- tests/plugins/strategy_react/test_strategy_react.py
- tests/plugins/strategy_react/test_provider_selection.py
- specs/plugin-llm_echo.spec
- pyproject.toml
- README.md
- docs/wiki/log.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/tool_bash/
- src/yaya/plugins/web/
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: AC-01 echo round-trip emits response with text tool_calls usage and request_id
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_echo_response_for_user_message
  Level: unit
  Given an echo plugin loaded with no configuration
  When a llm.call.request for provider echo with a user message hello is published
  Then a llm.call.response event is emitted with text equal to (echo) hello and zero token usage
  And the response echoes the originating request id

Scenario: Error path — unrelated provider id leaves the event uncommented by llm-echo
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_non_matching_provider_is_ignored
  Level: unit
  Given an echo plugin loaded with no configuration
  When a llm.call.request for a non-echo provider id is published
  Then no llm.call.response event is emitted by the llm-echo plugin
  And no llm.call.error event is emitted by the llm-echo plugin

Scenario: Empty messages list returns the deterministic no-input marker
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_empty_messages_returns_no_input_marker
  Level: unit
  Given an echo plugin loaded with no configuration
  When a llm.call.request for provider echo with an empty messages list is published
  Then a llm.call.response event is emitted with text equal to (echo) (no input)

Scenario: Multi-turn history echoes only the most recent user message
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_echoes_only_last_user_message
  Level: unit
  Given an echo plugin loaded with no configuration
  When a llm.call.request for provider echo carries multiple user turns ending in second is published
  Then a llm.call.response event is emitted with text equal to (echo) second

Scenario: Response request_id matches the originating event id for loop correlation
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_request_id_matches_source_event
  Level: unit
  Given an echo plugin loaded with no configuration
  When a llm.call.request for provider echo is published
  Then the llm.call.response event request_id field equals the originating request event id

Scenario: AC-AUTO strategy picks echo when no OPENAI_API_KEY is configured
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_provider_selection.py::test_picks_echo_when_no_api_key
  Level: unit
  Given the OPENAI_API_KEY environment variable is unset
  When the ReAct strategy decides the next step for an empty conversation
  Then the response next is llm and the chosen provider is echo

Scenario: AC-AUTO strategy picks openai when OPENAI_API_KEY is set
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_provider_selection.py::test_picks_openai_when_api_key_set
  Level: unit
  Given the OPENAI_API_KEY environment variable is set to a placeholder
  When the ReAct strategy decides the next step for an empty conversation
  Then the response next is llm and the chosen provider is openai

## Out of Scope

- Streaming (`assistant.message.delta` chunks). Parity with
  llm_openai today, which also emits one chunk.
- Token-budget accounting; the echo provider always reports zero.
- Configuration loading via `ctx.config` — replaced when #23 lands.
- Tool-call generation. The echo provider never emits `tool_calls`.
