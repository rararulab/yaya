Feature: Kernel AgentLoop fixed per-turn scheduler

  The AgentLoop drives one turn per user.message.received event through
  a frozen event sequence. Strategy plugins decide content; the loop
  decides order. Request/response pairs correlate by event id; guards
  for max_iterations and user.interrupt abort turns cleanly.

  Scenarios mirror specs/kernel-agent-loop.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: Happy path — user message drives AgentLoop to assistant.message.done
    Given an AgentLoop with a stub strategy that returns "llm" then "done"
    And a stub LLM provider returning "hello"
    When a user.message.received event is published
    Then an assistant.message.done event is observed with content "hello"
    And the frozen per-turn event sequence drove the turn

  Scenario: Tool round-trip emits tool.call.start and tool.call.result before assistant.message.done
    Given an AgentLoop with a strategy that returns "tool" then "llm" then "done"
    And a stub tool plugin that echoes its args
    And a stub LLM provider
    When a user.message.received event arrives
    Then a tool.call.start event and a tool.call.result event are observed
    And they occur before assistant.message.done in the frozen event sequence
    And the tool.call.request payload uses schema_version v1

  Scenario: Error path — max_iterations guard emits kernel.error and aborts the turn
    Given an AgentLoop configured with max_iterations=3
    And a strategy that never returns "done"
    When a user.message.received event arrives
    Then a kernel.error event is emitted carrying message "max_iterations_exceeded"
    And the turn aborts without assistant.message.done

  Scenario: Error path — user.interrupt guard aborts the active turn for the session
    Given an AgentLoop mid-turn awaiting a tool.call.result
    And the session worker is idle
    When a user.interrupt event is published for the same active session
    Then the current turn aborts under the interrupt guard
    And no further tool.call.request is emitted for that turn

  Scenario: v1 tool envelope projects into the ReAct Observation
    Given an AgentLoop with a registered v1 Tool returning a TextBlock envelope
    And a strategy that calls the tool then asks the LLM
    When a user.message.received event arrives
    Then the subsequent llm.call.request carries an Observation message with the tool's text

  Scenario: Correlation via request_id — untracked response without matching request_id is ignored
    Given an AgentLoop with an in-flight outbound request tracked by its event id
    When a response event arrives carrying no matching request_id correlation
    Then the untracked response is ignored and the loop keeps awaiting the correlated reply
