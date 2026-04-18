Feature: Echo LLM provider plugin

  These scenarios mirror specs/plugin-llm_echo.spec and keep the
  zero-config echo provider executable through the bus and strategy
  fallback path.

  Scenario: AC-01 echo round-trip emits response with text tool_calls usage and request_id
    Given an echo plugin loaded with no configuration
    When a llm.call.request for provider echo with a user message hello is published
    Then a llm.call.response event is emitted with text equal to (echo) hello and zero token usage
    And the response echoes the originating request id

  Scenario: Error path — unrelated provider id leaves the event uncommented by llm-echo
    Given an echo plugin loaded with no configuration
    When a llm.call.request for a non-echo provider id is published
    Then no llm.call.response event is emitted by the llm-echo plugin
    And no llm.call.error event is emitted by the llm-echo plugin

  Scenario: Empty messages list returns the deterministic no-input marker
    Given an echo plugin loaded with no configuration
    When a llm.call.request for provider echo with an empty messages list is published
    Then a llm.call.response event is emitted with text equal to (echo) (no input)

  Scenario: Multi-turn history echoes only the most recent user message
    Given an echo plugin loaded with no configuration
    When a llm.call.request for provider echo carries multiple user turns ending in second is published
    Then a llm.call.response event is emitted with text equal to (echo) second

  Scenario: Response request_id matches the originating event id for loop correlation
    Given an echo plugin loaded with no configuration
    When a llm.call.request for provider echo is published
    Then the llm.call.response event request_id field equals the originating request event id

  Scenario: AC-AUTO strategy picks echo when no OPENAI_API_KEY is configured
    Given the OPENAI_API_KEY environment variable is unset
    When the ReAct strategy decides the next step for an empty conversation
    Then the response next is llm and the chosen provider is echo

  Scenario: AC-AUTO strategy picks openai when OPENAI_API_KEY is set
    Given the OPENAI_API_KEY environment variable is set to a placeholder
    When the ReAct strategy decides the next step for an empty conversation
    Then the response next is llm and the chosen provider is openai
