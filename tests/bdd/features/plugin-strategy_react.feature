Feature: ReAct strategy plugin

  The executable Gherkin mirror of specs/plugin-strategy_react.spec.

  Scenario: No assistant message yet decides next step is llm with configured provider and model
    Given a strategy.decide.request whose state has no prior assistant message
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next llm and the configured provider and model
    And the response echoes the originating request id

  Scenario: Pending tool_calls on the assistant message decide next step is tool with first tool call
    Given a strategy.decide.request whose last assistant message carries a non-empty tool_calls list
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next tool and the first pending tool_call payload
    And the response echoes the originating request id

  Scenario: Tool result just landed decides next step loops back to llm for another pass
    Given a strategy.decide.request whose last_tool_result is populated after an assistant step
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next llm and the configured provider and model
    And the response echoes the originating request id

  Scenario: Assistant message without tool_calls or pending tool result decides next step is done
    Given a strategy.decide.request whose last assistant message has no tool_calls and no pending tool result
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next done
    And the response echoes the originating request id

  Scenario: Error path — strategy.decide.request missing state payload raises for plugin.error synthesis
    Given a strategy.decide.request whose payload omits the state key entirely
    When the ReAct plugin handles the event
    Then the handler raises ValueError so the kernel synthesizes a plugin.error
