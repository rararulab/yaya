spec: task
name: "plugin-tool_bash"
inherits: project
tags: [plugin, tool]
---

## Intent

The bash tool plugin runs a caller-supplied argv list as a child
subprocess and returns stdout, stderr, and return code on the yaya
bus. It subscribes to `tool.call.request`, filters for
`name == "bash"`, validates that `args.cmd` is a list of strings,
enforces a wall-clock timeout, and emits exactly one
`tool.call.result` per request. By construction the plugin never
uses `shell=True` — an argv list eliminates the shell-injection
class of hazards.

## Decisions

- Subscribes only to `tool.call.request`; handler returns silently
  when `payload["name"]` is not `"bash"` so sibling tool plugins can
  coexist on the same subscription without contention.
- The happy-path scenario runs the argv list through
  `asyncio.create_subprocess_exec` with stdout and stderr captured,
  so the `tool.call.result` payload observably carries the child
  subprocess stdout, stderr, and returncode. `shell=True` is never
  passed, a project-wide invariant asserted in CI grep guards.
- Invalid `cmd` (not a `list[str]`) produces `tool.call.result`
  with `{"ok": False, "error": "cmd must be argv list (list of
  strings)"}` before any subprocess spawn.
- The wall-clock timeout defaults to 30 seconds via
  `asyncio.wait_for`; on overrun the subprocess is killed and the
  plugin emits `{"ok": False, "error": "timeout"}`. Overridable
  through the plugin constructor for tests.
- Every `tool.call.result` echoes `request_id` from the
  originating request and carries the logical tool-call `id` from
  the request payload so the agent loop correlates concurrent
  tool calls.

## Boundaries

### Allowed Changes
- src/yaya/plugins/tool_bash/__init__.py
- src/yaya/plugins/tool_bash/plugin.py
- src/yaya/plugins/tool_bash/AGENT.md
- tests/plugins/tool_bash/__init__.py
- tests/plugins/tool_bash/test_tool_bash.py
- specs/plugin-tool_bash.spec

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Happy path — argv list runs the subprocess and emits stdout stderr returncode
  Test:
    Package: yaya
    Filter: tests/plugins/tool_bash/test_tool_bash.py::test_argv_list_runs_and_emits_result
  Level: integration
  Given a loaded tool-bash plugin
  When a tool.call.request is published with name bash and args cmd equal to the echo argv list
  Then a tool.call.result event is emitted with ok true and value carrying stdout stderr and returncode zero
  And the response echoes the originating request id

Scenario: Error path — cmd not a list of strings emits ok false with argv validation error
  Test:
    Package: yaya
    Filter: tests/plugins/tool_bash/test_tool_bash.py::test_cmd_not_list_emits_validation_error
  Level: unit
  Given a loaded tool-bash plugin
  When a tool.call.request is published with name bash and args cmd equal to a single string not a list
  Then a tool.call.result event is emitted with ok false and error mentioning argv list
  And no subprocess is spawned

Scenario: Error path — command exceeding the timeout is killed and emits ok false timeout
  Test:
    Package: yaya
    Filter: tests/plugins/tool_bash/test_tool_bash.py::test_timeout_kills_process_and_emits_timeout
  Level: integration
  Given a loaded tool-bash plugin whose timeout is reduced to a small value
  When a tool.call.request runs a sleep command longer than the timeout
  Then the subprocess is killed by the plugin
  And a tool.call.result event is emitted with ok false and error timeout

Scenario: Non-bash tool name is ignored by the tool-bash plugin
  Test:
    Package: yaya
    Filter: tests/plugins/tool_bash/test_tool_bash.py::test_non_bash_tool_name_ignored
  Level: unit
  Given a loaded tool-bash plugin
  When a tool.call.request for a different tool name is published
  Then no tool.call.result event is emitted by the tool-bash plugin

## Out of Scope

- Streaming stdout/stderr while the process runs (adapters handle
  that via `tool.call.start` today).
- Shell features like pipes / redirects / globs — use a
  higher-level tool plugin or pre-expand in the caller.
- Sandbox / capability restrictions (2.0 scope).
