spec: task
name: "llm-provider-contract"
tags: [kernel, llm, protocol]
---

## Intent

The yaya kernel defines a production-grade llm-provider contract (v1):
providers implement a streaming :class:`LLMProvider` Protocol that
returns a :class:`StreamedMessage` — an async iterator of content /
tool-call parts plus a terminal :class:`TokenUsage`. Token usage
mirrors kimi-cli's cache-aware layout so Anthropic prompt-caching
accounting is free. A closed :class:`ChatProviderError` hierarchy
(connection, timeout, status, empty) plus SDK-to-taxonomy converters
for ``openai``, ``anthropic``, and raw ``httpx`` keep error handling
typed at the kernel boundary. An optional
:class:`RetryableChatProvider` hook shape is present for a future
retry runtime. The SDK-only rule (providers must use the official
``openai`` or ``anthropic`` SDK — never raw HTTP) is enforced
mechanically by extending ``scripts/check_banned_frameworks.py``.

## Decisions

- ``src/yaya/kernel/llm.py`` owns the contract: :class:`LLMProvider`
  Protocol, :class:`StreamedMessage` Protocol, :class:`StreamPart`
  union (``ContentPart`` | ``ToolCallPart``), :class:`TokenUsage`
  pydantic model, :data:`ThinkingEffort` Literal, the
  :class:`ChatProviderError` hierarchy, three SDK-error converters,
  and the :class:`RetryableChatProvider` Protocol.
- :class:`LLMProvider` is a :class:`~typing.Protocol`, not a pydantic
  ``BaseModel`` — providers are stateful and own SDK clients /
  credentials. The tool contract uses ``BaseModel`` because tools are
  stateless request objects; providers are different shapes with the
  same discipline.
- :class:`TokenUsage` splits input tokens into ``input_other``,
  ``input_cache_read``, ``input_cache_creation``. Derived ``input``
  and ``total`` are emitted through a
  :func:`pydantic.model_serializer` wrap (not ``@computed_field`` +
  ``@property``) so the type-checker story is symmetric across mypy
  and pyright.
- Converters import the SDKs lazily inside the function so a missing
  install (``anthropic`` is a soft dep) does not crash the kernel at
  import time — unknown inputs degrade to a generic
  :class:`ChatProviderError` carrying ``str(exc)``.
- Event catalog gains ``llm.call.delta`` (new public kind; added to
  ``docs/dev/plugin-protocol.md`` and ``PublicEventKind``).
  :class:`LlmCallResponsePayload` picks up a serialised ``usage``
  dict; :class:`LlmCallErrorPayload` picks up optional ``kind``
  (``connection`` | ``timeout`` | ``status`` | ``empty`` | ``other``)
  and ``status_code``. Legacy providers that omit ``kind`` are still
  valid — consumers treat missing ``kind`` as ``"other"``.
- ``scripts/check_banned_frameworks.py`` gets a new scan:
  ``check_llm_plugin_imports`` walks ``src/yaya/plugins/llm_*/**/*.py``
  and rejects any direct import of ``httpx`` / ``requests`` /
  ``aiohttp``. The ban is deliberately scoped to ``llm_*`` — the
  ``openai`` and ``anthropic`` SDKs use ``httpx`` internally, and
  unrelated plugin categories may use ``httpx`` for legitimate work.
- Bundled ``llm_openai`` and ``llm_echo`` stay on the legacy
  subscribe-to-``llm.call.request`` path in this PR. Migration to
  the v1 contract is a follow-up, same discipline as the tool-contract
  migration for ``tool_bash`` (see ``specs/tool-contract.spec``).
- :class:`RetryableChatProvider.on_retryable_error` is a frozen shape;
  the runtime that consumes it lands in a follow-up PR once at least
  one provider implements it.

## Boundaries

### Allowed Changes
- src/yaya/kernel/llm.py
- src/yaya/kernel/events.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- scripts/check_banned_frameworks.py
- tests/kernel/test_llm.py
- tests/kernel/test_events.py
- tests/scripts/test_check_llm_plugin_imports.py
- docs/dev/plugin-protocol.md
- docs/wiki/log.md
- specs/llm-provider-contract.spec

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/llm_echo/
- pyproject.toml
- GOAL.md

## Completion Criteria

Scenario: AC-01 — TokenUsage carries Anthropic cache counters and derives totals
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_token_usage_cache_math
  Level: unit
  Given a TokenUsage with input_other 3 input_cache_read 2 input_cache_creation 1 and output 4
  When input and total are read
  Then input equals 6 and total equals 10

Scenario: AC-02 — openai API timeout is translated to APITimeoutError
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_openai_converter_timeout
  Level: unit
  Given an openai APITimeoutError raised by the SDK
  When openai_to_chat_provider_error is called
  Then an APITimeoutError instance is returned

Scenario: AC-03 — openai APIStatusError preserves status_code
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_openai_converter_status
  Level: unit
  Given an openai APIStatusError with status_code 429
  When openai_to_chat_provider_error is called
  Then the returned APIStatusError carries status_code 429

Scenario: AC-04 — anthropic typed errors are translated via a stub SDK
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_anthropic_converter_maps_typed_errors
  Level: unit
  Given a stub anthropic module exposing APIConnectionError APITimeoutError and APIStatusError
  When anthropic_to_chat_provider_error is called for each typed error
  Then the returned exception is the matching yaya taxonomy subclass

Scenario: AC-05 — raw httpx connect errors are translated to APIConnectionError
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_convert_httpx_connect_error
  Level: unit
  Given an httpx ConnectError instance
  When convert_httpx_error is called
  Then an APIConnectionError is returned

Scenario: AC-06 — raw httpx read timeouts are translated to APITimeoutError
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_convert_httpx_timeout
  Level: unit
  Given an httpx ReadTimeout instance
  When convert_httpx_error is called
  Then an APITimeoutError is returned

Scenario: AC-07 — LLMProvider Protocol is runtime-checkable
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_llm_provider_protocol_is_runtime_checkable
  Level: unit
  Given a concrete stub with name model_name thinking_effort and an async generate method
  When isinstance is called against LLMProvider
  Then the stub is recognised as an LLMProvider

Scenario: AC-08 — Streaming provider emits deltas and a final response through the bus
  Test:
    Package: yaya
    Filter: tests/kernel/test_llm.py::test_fake_provider_streams_through_bus
  Level: integration
  Given a fake provider yielding two ContentParts hel and lo
  When the kernel publishes llm.call.request and the provider re-publishes deltas and a terminal response
  Then two llm.call.delta events are observed and one llm.call.response carries the merged text hello with a serialised TokenUsage

Scenario: AC-09 — LLM plugins importing raw httpx are rejected by the scanner
  Test:
    Package: yaya
    Filter: tests/scripts/test_check_llm_plugin_imports.py::test_injected_httpx_import_is_flagged
  Level: unit
  Given a fake llm_fake plugin that imports httpx
  When check_llm_plugin_imports is run against its src root
  Then an llm-plugin-import violation is reported naming httpx and the plugin file

Scenario: AC-10 — Non-LLM plugins may use httpx without violating the ban
  Test:
    Package: yaya
    Filter: tests/scripts/test_check_llm_plugin_imports.py::test_non_llm_plugin_is_not_flagged
  Level: unit
  Given a tool_http plugin that imports httpx
  When check_llm_plugin_imports is run against its src root
  Then no violation is reported

## Out of Scope

- Migrating llm_openai or llm_echo to the v1 contract — they remain
  on the legacy ``on_event`` subscribe path until a follow-up PR.
- Wiring the agent loop to consume ``llm.call.delta`` for streaming
  (separate issue / loop refactor).
- Implementing the retry runtime that consumes
  :class:`RetryableChatProvider` — the Protocol shape is frozen here
  but the dispatcher lands separately.
- Shipping a concrete Anthropic provider — the
  ``anthropic_to_chat_provider_error`` converter is in place so the
  provider plugin can land without churning this contract.
