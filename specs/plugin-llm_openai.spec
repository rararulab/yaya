spec: task
name: "plugin-llm_openai"
tags: [plugin, llm-provider]
---

## Intent

The OpenAI LLM-provider plugin speaks to the official OpenAI Chat
Completions API through the async SDK and surfaces its result on the
yaya bus. It subscribes to `llm.call.request`, filters by *instance
id* (after D4b every provider is instance-scoped — one plugin backs
many configured records under `providers.<id>.*`) so sibling
providers coexist, and emits either a `llm.call.response` on
success or a `llm.call.error` on any SDK failure. An unconfigured
instance id simply sees no response from this plugin — the bus
surfaces a "no subscriber" silence rather than a synthetic error.

## Decisions

- Subscribes to `llm.call.request` and `config.updated`. The handler
  returns silently when `payload["provider"]` is not an instance id
  in `self._clients` so other provider plugins own their own traffic
  on the same subscription.
- Credentials per instance: `providers.<id>.api_key` wins, falling
  back to `OPENAI_API_KEY` when unset. `providers.<id>.base_url`
  overrides `OPENAI_BASE_URL`. Instances with neither key nor env
  fallback log a WARNING at load and are simply absent from
  `self._clients` — requests naming them get the bus-level "no
  subscriber" silence.
- On success the plugin calls `openai.AsyncOpenAI.chat.completions.create`
  non-streaming and emits a `llm.call.response` event that echoes
  `request_id` plus `text`, `tool_calls`, and `usage` with
  `input_tokens` and `output_tokens`.
- `RateLimitError` is translated to `llm.call.error` with an
  optional `retry_after_s` extracted from the response `retry-after`
  header; any other SDK or generic exception produces
  `llm.call.error` with `str(exc)`.
- `asyncio.CancelledError` propagates out of the handler
  unchanged so `asyncio.wait_for` and task cancellation unwind
  cleanly; `on_unload` drops the per-instance client dict without
  awaiting `close()` so in-flight dispatches finish cleanly.

## Boundaries

### Allowed Changes
- src/yaya/plugins/llm_openai/__init__.py
- src/yaya/plugins/llm_openai/plugin.py
- src/yaya/plugins/llm_openai/AGENT.md
- tests/plugins/llm_openai/__init__.py
- tests/plugins/llm_openai/test_llm_openai.py
- specs/plugin-llm_openai.spec

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/tool_bash/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Successful completion emits llm.call.response with text tool_calls usage and request_id
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_successful_completion_emits_response
  Level: unit
  Given a configured llm-openai plugin with a stubbed AsyncOpenAI client
  When a llm.call.request for provider openai is published
  Then the stubbed chat completions create method is called with the request model and messages
  And a llm.call.response event is emitted carrying text tool_calls usage and the originating request id

Scenario: Missing API key and no configured instance leaves the request silent
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_missing_api_key_emits_not_configured_error
  Level: unit
  Given an llm-openai plugin loaded with no OPENAI_API_KEY environment variable
  When a llm.call.request for provider openai is published
  Then no llm.call.response event is emitted by the llm-openai plugin
  And no llm.call.error event is emitted by the llm-openai plugin

Scenario: Error path — unrelated provider id leaves the event uncommented by llm-openai
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_non_matching_provider_is_ignored
  Level: unit
  Given a configured llm-openai plugin with a stubbed AsyncOpenAI client
  When a llm.call.request for a non-openai provider id is published
  Then no llm.call.response event is emitted by the llm-openai plugin
  And no llm.call.error event is emitted by the llm-openai plugin

Scenario: Error path — SDK rate-limit error translates to llm.call.error with retry hint
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_rate_limit_error_emits_error_event
  Level: unit
  Given a configured llm-openai plugin whose stubbed client raises a RateLimitError
  When a llm.call.request for provider openai is published
  Then a llm.call.error event is emitted with the error string and the originating request id

## Out of Scope

- Streaming (`assistant.message.delta` chunks) — follows the adapter
  work.
- Token-budget accounting beyond what the SDK's `usage` object
  returns.
- Automatic retry on transient failures — the strategy plugin
  decides whether to retry.
