Feature: Tool contract

  These scenarios mirror specs/tool-contract.spec and keep the v1 tool
  contract executable: validation, schema generation, serialization,
  dispatch, duplicate registration, legacy bypass, and approval
  rejection.

  Scenario: AC-01 — tool params are validated before run
    Given a Tool subclass with a typed int field count
    When a tool call request is emitted with schema version v1 and args count equal to the string abc
    Then a tool error event is emitted with kind validation
    And the tool run method is not invoked

  Scenario: AC-02 — tool returns ToolOk with a display block
    Given a Tool subclass whose run method returns ToolOk with a TextBlock display
    When a tool call request is published with schema version v1
    Then a tool call result event is emitted with ok true and the envelope carries a TextBlock

  Scenario: AC-03 — tool JSON schema is LLM function call compatible
    Given a Tool subclass with pydantic fields
    When openai function spec is invoked on the class
    Then the returned dict carries name description and a parameters JSON schema with required field entries

  Scenario: AC-04 — unknown tool name is rejected with kind not_found
    Given no tool is registered under the name nope
    When a tool call request is published with schema version v1 and name nope
    Then a tool error event is emitted with kind not_found

  Scenario: AC-05 — schema version v1 envelope round trip
    Given a ToolOk instance with a TextBlock display
    When model dump is invoked and the resulting dict is validated back
    Then the round trip returns an equivalent ToolOk instance

  Scenario: AC-07 — dispatcher ignores legacy payloads without schema_version so on_event tools keep working
    Given a v1 tool registered under the name echo
    When a tool call request without schema version is published for the echo name
    Then the dispatcher emits no tool call result and no tool error so the legacy on_event path owns the delivery

  Scenario: AC-08 — register_tool raises ToolAlreadyRegisteredError on duplicate v1 registration
    Given a v1 Tool subclass already registered under the name echo
    When register_tool is called with a different subclass claiming the same echo name
    Then ToolAlreadyRegisteredError is raised before the registry is mutated

  Scenario: AC-09 — Tool.openai_function_spec produces a dict with name description and parameters schema
    Given a Tool subclass with ClassVar name and description plus pydantic fields
    When Tool.openai_function_spec is invoked on the class
    Then the resulting dict carries the name description and parameters JSON schema fit for LLM function calling

  Scenario: AC-06 — requires approval plus denied pre approve is rejected
    Given a Tool subclass with requires approval true whose pre approve returns false
    When a tool call request is published with schema version v1
    Then a tool error event is emitted with kind rejected
    And no tool call result event is emitted
