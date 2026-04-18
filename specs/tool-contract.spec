spec: task
name: "tool-contract"
tags: [kernel, tool, protocol]
---

## Intent

The yaya kernel defines a production-grade tool contract (v1): tools
are pydantic `BaseModel` subclasses that declare their parameters as
typed fields; the JSON schema surfaced to the LLM is auto-derived.
Tool results flow back through a typed `ToolOk` / `ToolError`
envelope carrying a terse `brief` for logs plus a `DisplayBlock`
rendering hint for adapters. A kernel-side dispatcher validates
parameters against the schema **before** the tool's own code runs, so
plugin authors never see malformed input. A `schema_version="v1"`
toggle on `tool.call.request` selects the new path; legacy tool
plugins that subscribe directly via `on_event` keep working.

## Decisions

- `src/yaya/kernel/tool.py` owns the contract: `Tool` base class,
  `ToolOk` / `ToolError` envelope, `DisplayBlock` hierarchy with
  `TextBlock` / `MarkdownBlock` / `JsonBlock`, the module-level
  `ToolRegistry` (`register_tool`, `get_tool`), and the `dispatch`
  coroutine wired to `tool.call.request` via `install_dispatcher`.
- `Tool.openai_function_spec()` produces a `{"name", "description",
  "parameters"}` dict compatible with the OpenAI chat-completions
  `tools` array; the `parameters` schema is
  `model_json_schema()` with its auto-generated top-level `title`
  stripped.
- The dispatcher acts **only** on `tool.call.request` payloads that
  carry `schema_version == "v1"`; payloads without that field are left
  to whatever plugin subscribed via `on_event`. This is the backward
  compatibility hinge that keeps `tool_bash` and its tests working
  unchanged in this PR.
- Validation failures produce a `tool.error` event with `kind =
  "validation"` and `detail.errors` carrying pydantic's structured
  error list; the target tool's `run()` is never called.
- Unknown tool names produce `tool.error` with `kind = "not_found"`;
  denied approvals produce `kind = "rejected"`. `tool.error` is a new
  public event kind added to the closed catalog in this PR.
- The approval gate is the `requires_approval` ClassVar plus the
  async `pre_approve` hook on the `Tool` base; returning `False`
  produces a `tool.error` with `kind="rejected"`. Default
  `pre_approve` returns `True` (no-approval); the real approval
  runtime lands in #28.
- `register_tool` raises `ToolAlreadyRegisteredError` on a duplicate
  v1 registration; when a name is already claimed by a legacy plugin
  the registry logs a WARNING but does not crash — backward compat
  wins over strict migration.

## Boundaries

### Allowed Changes
- src/yaya/kernel/tool.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/events.py
- src/yaya/kernel/AGENT.md
- tests/kernel/test_tool.py
- tests/kernel/test_events.py
- docs/dev/plugin-protocol.md
- specs/tool-contract.spec

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- GOAL.md

## Completion Criteria

Scenario: AC-01 — tool params are validated before run
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_dispatcher_validation_failure_emits_tool_error_and_skips_run
  Level: unit
  Given a Tool subclass with a typed int field count
  When a tool call request is emitted with schema version v1 and args count equal to the string abc
  Then a tool error event is emitted with kind validation
  And the tool run method is not invoked

Scenario: AC-02 — tool returns ToolOk with a display block
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_dispatcher_happy_path
  Level: integration
  Given a Tool subclass whose run method returns ToolOk with a TextBlock display
  When a tool call request is published with schema version v1
  Then a tool call result event is emitted with ok true and the envelope carries a TextBlock

Scenario: AC-03 — tool JSON schema is LLM function call compatible
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_openai_function_spec_includes_name_description_and_schema
  Level: unit
  Given a Tool subclass with pydantic fields
  When openai function spec is invoked on the class
  Then the returned dict carries name description and a parameters JSON schema with required field entries

Scenario: AC-04 — unknown tool name is rejected with kind not_found
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_dispatcher_unknown_name_emits_not_found
  Level: unit
  Given no tool is registered under the name nope
  When a tool call request is published with schema version v1 and name nope
  Then a tool error event is emitted with kind not_found

Scenario: AC-05 — schema version v1 envelope round trip
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_tool_ok_serialization_roundtrips
  Level: unit
  Given a ToolOk instance with a TextBlock display
  When model dump is invoked and the resulting dict is validated back
  Then the round trip returns an equivalent ToolOk instance

Scenario: AC-07 — dispatcher ignores legacy payloads without schema_version so on_event tools keep working
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_dispatcher_skips_legacy_payloads
  Level: unit
  Given a v1 tool registered under the name echo
  When a tool call request without schema version is published for the echo name
  Then the dispatcher emits no tool call result and no tool error so the legacy on_event path owns the delivery

Scenario: AC-08 — register_tool raises ToolAlreadyRegisteredError on duplicate v1 registration
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_double_register_different_class_raises
  Level: unit
  Given a v1 Tool subclass already registered under the name echo
  When register_tool is called with a different subclass claiming the same echo name
  Then ToolAlreadyRegisteredError is raised before the registry is mutated

Scenario: AC-09 — Tool.openai_function_spec produces a dict with name description and parameters schema
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_openai_function_spec_includes_name_description_and_schema
  Level: unit
  Given a Tool subclass with ClassVar name and description plus pydantic fields
  When Tool.openai_function_spec is invoked on the class
  Then the resulting dict carries the name description and parameters JSON schema fit for LLM function calling

Scenario: AC-06 — requires approval plus denied pre approve is rejected
  Test:
    Package: yaya
    Filter: tests/kernel/test_tool.py::test_dispatcher_rejected_by_pre_approve
  Level: unit
  Given a Tool subclass with requires approval true whose pre approve returns false
  When a tool call request is published with schema version v1
  Then a tool error event is emitted with kind rejected
  And no tool call result event is emitted

## Out of Scope

- Approval runtime — prompt the user, cancel on interrupt, resume on
  approval (tracked in #28).
- MCP tool bridge (tracked in #31).
- Migrating the bundled `tool_bash` plugin to the v1 contract — it
  remains on the legacy `on_event` path until a follow-up PR.
- Wiring the agent loop to dispatch v1 tool calls directly; the loop
  still routes `tool.call.request` through the bus for now.
