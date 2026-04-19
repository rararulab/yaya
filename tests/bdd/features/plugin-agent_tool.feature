Feature: Agent tool plugin — multi-agent via forked session + event bus

  The executable Gherkin mirror of specs/plugin-agent_tool.spec.

  Scenario: Happy path — forked sub-agent returns its final text as a ToolOk
    Given a loaded agent-tool plugin with a fake agent loop answering "42"
    When a tool.call.request for name agent with goal is published
    Then a tool.call.result event is emitted with ok true and final text 42
    And the child session id is prefixed with parent::agent::

  Scenario: Parent tape stays immutable after the child runs
    Given a loaded agent-tool plugin and a baseline parent tape length
    When a sub-agent completes one turn on the forked child session
    Then the parent tape length is unchanged

  Scenario: Depth guard blocks runaway recursion before any fork
    Given a parent session whose id already carries max_depth hops
    When a tool.call.request for agent is dispatched on that session
    Then a tool.call.result with ok false and kind rejected mentioning max depth is emitted

  Scenario: Timeout surfaces as ToolError kind timeout
    Given a loaded agent-tool plugin and a fake loop that never finishes
    When a sub-agent is spawned with a sub-second max_wall_seconds
    Then a tool.call.result with ok false and kind timeout is emitted
    And x.agent.subagent.failed with reason timeout is emitted

  Scenario: Tool allowlist records forbidden hits and narrows via extension event
    Given a loaded agent-tool plugin and a fake loop that issues a forbidden tool call
    When a sub-agent is spawned with a non-empty tools allowlist that excludes the tool
    Then x.agent.allowlist.narrowed is emitted listing the attempted and allowed tool names
    And x.agent.subagent.completed records the forbidden hit

  Scenario: Cancellation during run emits a failed event with reason cancelled
    Given a running AgentTool awaiting a sub-agent that will never finish
    When the running task is cancelled
    Then x.agent.subagent.failed with reason cancelled is emitted

  Scenario: The agent tool requires approval by default
    Given the AgentTool class
    When its requires_approval flag is read
    Then requires_approval is true and name equals agent
