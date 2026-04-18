Feature: MCP bridge plugin

  The executable Gherkin mirror of specs/plugin-mcp_bridge.spec.

  Scenario: Discovers tools from a fake MCP server and registers them by qualified name
    Given a fake MCP server exposing one tool echo with a string parameter
    When the bridge loads with that server configured
    Then the kernel tool registry contains a tool named mcp_local_echo
    And an x.mcp.server.ready event is emitted naming the server and its tools

  Scenario: Approval default is True for every MCP derived tool
    Given a fake MCP server descriptor for a tool named echo
    When the tool factory builds a yaya Tool subclass with the bridge default
    Then the resulting class requires approval

  Scenario: Tool call forwards arguments and wraps the response in ToolOk
    Given a loaded bridge with a fake MCP server exposing echo
    When the echo tool is invoked through the kernel dispatcher with an approved gate
    Then the dispatcher emits one tool.call.result with ok true
    And the envelope brief reflects the echoed text

  Scenario: Boot failure exhausts retries and emits x mcp server error
    Given a fake MCP server factory that always fails to start
    When the bridge loads with retries collapsed
    Then no tools are registered for the failing server
    And exactly one x.mcp.server.error event is emitted with kind boot_failed

  Scenario: Bad config entry surfaces as x mcp server error without tainting siblings
    Given a configuration mixing one valid server and one server whose command field is empty
    When the bridge loads
    Then an x.mcp.server.error event is emitted naming the bad server
    And the valid server still registers its tool

  Scenario: Unload closes every spawned client deterministically
    Given a loaded bridge with two fake MCP servers
    When on_unload runs
    Then close has been awaited on both client instances
