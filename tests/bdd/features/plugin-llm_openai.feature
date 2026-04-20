Feature: OpenAI LLM provider plugin

  The executable Gherkin mirror of specs/plugin-llm_openai.spec.

  Scenario: Successful completion emits llm.call.response with text tool_calls usage and request_id
    Given a configured llm-openai plugin with a stubbed AsyncOpenAI client
    When a llm.call.request for provider openai is published
    Then the stubbed chat completions create method is called with the request model and messages
    And a llm.call.response event is emitted carrying text tool_calls usage and the originating request id

  Scenario: Missing API key and no configured instance leaves the request silent
    Given an llm-openai plugin loaded with no OPENAI_API_KEY environment variable
    When a llm.call.request for provider openai is published
    Then no llm.call.response event is emitted by the llm-openai plugin
    And no llm.call.error event is emitted by the llm-openai plugin

  Scenario: Error path — unrelated provider id leaves the event uncommented by llm-openai
    Given a configured llm-openai plugin with a stubbed AsyncOpenAI client
    When a llm.call.request for a non-openai provider id is published
    Then no llm.call.response event is emitted by the llm-openai plugin
    And no llm.call.error event is emitted by the llm-openai plugin

  Scenario: Error path — SDK rate-limit error translates to llm.call.error with retry hint
    Given a configured llm-openai plugin whose stubbed client raises a RateLimitError
    When a llm.call.request for provider openai is published
    Then a llm.call.error event is emitted with the error string and the originating request id
