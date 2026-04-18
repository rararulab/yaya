Feature: LLM provider contract

  These scenarios mirror specs/llm-provider-contract.spec and keep the
  TokenUsage model, SDK-error converters, protocol shape, stream
  semantics, and llm-plugin import scanner executable.

  Scenario: AC-01 — TokenUsage carries Anthropic cache counters and derives totals
    Given a TokenUsage with input_other 3 input_cache_read 2 input_cache_creation 1 and output 4
    When input and total are read
    Then input equals 6 and total equals 10

  Scenario: AC-02 — openai API timeout is translated to APITimeoutError
    Given an openai APITimeoutError raised by the SDK
    When openai_to_chat_provider_error is called
    Then an APITimeoutError instance is returned

  Scenario: AC-03 — openai APIStatusError preserves status_code
    Given an openai APIStatusError with status_code 429
    When openai_to_chat_provider_error is called
    Then the returned APIStatusError carries status_code 429

  Scenario: AC-04 — anthropic typed errors are translated via a stub SDK
    Given a stub anthropic module exposing APIConnectionError APITimeoutError and APIStatusError
    When anthropic_to_chat_provider_error is called for each typed error
    Then the returned exception is the matching yaya taxonomy subclass

  Scenario: AC-05 — raw httpx connect errors are translated to APIConnectionError
    Given an httpx ConnectError instance
    When convert_httpx_error is called
    Then an APIConnectionError is returned

  Scenario: AC-06 — raw httpx read timeouts are translated to APITimeoutError
    Given an httpx ReadTimeout instance
    When convert_httpx_error is called
    Then an APITimeoutError is returned

  Scenario: AC-07 — LLMProvider Protocol is runtime-checkable
    Given a concrete stub with name model_name thinking_effort and an async generate method
    When isinstance is called against LLMProvider
    Then the stub is recognised as an LLMProvider

  Scenario: AC-08 — Streaming provider emits deltas and a final response through the bus
    Given a fake provider yielding two ContentParts hel and lo
    When the kernel publishes llm.call.request and the provider re-publishes deltas and a terminal response
    Then two llm.call.delta events are observed and one llm.call.response carries the merged text hello with a serialised TokenUsage

  Scenario: AC-09 — LLM plugins importing raw httpx are rejected by the scanner
    Given a fake llm_fake plugin that imports httpx
    When check_llm_plugin_imports is run against its src root
    Then an llm-plugin-import violation is reported naming httpx and the plugin file

  Scenario: AC-10 — Non-LLM plugins may use httpx without violating the ban
    Given a tool_http plugin that imports httpx
    When check_llm_plugin_imports is run against its src root
    Then no violation is reported
