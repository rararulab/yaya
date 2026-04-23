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

  Scenario: Assistant message with a bracketed tool-call block decides next step is tool
    Given a strategy.decide.request whose last assistant message contains Final Answer prose and a [TOOL_CALL] block
    When the ReAct plugin handles the event
    Then a strategy.decide.response is emitted with next tool and the parsed tool_call payload
    And the Final Answer prose does not terminate the turn before the tool runs
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

  Scenario: Shopping output contract appears only when mercari_jp_search is registered
    Given the live tool registry contains mercari_jp_search
    When the ReAct system prompt is composed
    Then it pins the Final Answer to the required 3-row markdown table shape
