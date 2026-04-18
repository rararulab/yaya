Feature: Bash tool plugin

  The executable Gherkin mirror of specs/plugin-tool_bash.spec.

  Scenario: Happy path — argv list runs the subprocess and emits stdout stderr returncode
    Given a loaded tool-bash plugin
    When a tool.call.request is published with name bash and args cmd equal to the echo argv list
    Then a tool.call.result event is emitted with ok true and value carrying stdout stderr and returncode zero
    And the response echoes the originating request id

  Scenario: Error path — cmd not a list of strings emits ok false with argv validation error
    Given a loaded tool-bash plugin
    When a tool.call.request is published with name bash and args cmd equal to a single string not a list
    Then a tool.call.result event is emitted with ok false and error mentioning argv list
    And no subprocess is spawned

  Scenario: Error path — command exceeding the timeout is killed and emits ok false timeout
    Given a loaded tool-bash plugin whose timeout is reduced to a small value
    When a tool.call.request runs a sleep command longer than the timeout
    Then the subprocess is killed by the plugin
    And a tool.call.result event is emitted with ok false and error timeout

  Scenario: Non-bash tool name is ignored by the tool-bash plugin
    Given a loaded tool-bash plugin
    When a tool.call.request for a different tool name is published
    Then no tool.call.result event is emitted by the tool-bash plugin
