Feature: ReAct strategy plugin

  The executable Gherkin mirror of specs/plugin-strategy_react.spec.

  Scenario: No assistant message yet decides next step is llm with configured provider and model
    Given a strategy.decide.request whose state has no prior assistant message
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next llm and the configured provider and model
    And the response echoes the originating request id

  Scenario: Assistant message with a well-formed Action decides next step is tool with the parsed tool_call
    Given a strategy.decide.request whose last assistant message contains a ReAct Action and Action Input
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next tool and the parsed tool_call payload
    And the response echoes the originating request id

  Scenario: Observation follows the last assistant message so the next step loops back to llm
    Given a strategy.decide.request whose state has an Observation user message after the last assistant message
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next llm and the configured provider and model
    And the response echoes the originating request id

  Scenario: Assistant message with a Final Answer label decides next step is done
    Given a strategy.decide.request whose last assistant message contains a Final Answer label
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next done
    And the response echoes the originating request id

  Scenario: Error path — strategy.decide.request missing state payload raises for plugin.error synthesis
    Given a strategy.decide.request whose payload omits the state key entirely
    When the ReAct plugin handles the event
    Then the handler raises ValueError so the kernel synthesizes a plugin.error
