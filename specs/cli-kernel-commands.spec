spec: task
name: "cli-kernel-commands"
tags: [cli, kernel, bdd]
---

## Intent

yaya's CLI surface at 1.0 is deliberately minimal: five kernel
built-ins that exist only to bootstrap the kernel and manage plugins.
Every other capability — adapters, tools, strategies, memory, LLM
providers — ships as a plugin. This contract pins down the three new
built-ins landing alongside the existing `version` and `update`:
`serve`, `hello`, and `plugin {list, install, remove}`. Adding a new
built-in subcommand would require a `GOAL.md` amendment.

## Decisions

- `yaya serve` binds **only** `127.0.0.1`; there is no `--host`
  flag. `--port 0` auto-picks a free port via an OS `bind` probe on
  the loopback interface. Kernel boot order: EventBus → PluginRegistry
  → AgentLoop; shutdown reverses. Signal handling uses
  `asyncio.add_signal_handler` (not `signal.signal`). Under `--json`
  the command emits two lifecycle events: `serve.started` after
  bindings are live and `shutdown` on clean exit.
- Browser launch is best-effort and conditional: only when
  `--no-open` is unset AND `registry.snapshot()` shows a loaded
  `adapter` plugin whose name starts with `web`. When absent, a
  stderr warning tells the operator the kernel is running headless;
  `serve` does NOT fail.
- `yaya hello` is the kernel smoke-test. It subscribes a sentinel to
  `user.message.received`, emits one synthetic event, waits 5
  seconds, then emits `{"ok": true, "action": "hello", "received":
  true}` on success or `{"ok": false, "error":
  "event_bus_unresponsive", "suggestion": ...}` on timeout. No LLM
  or adapter plugin is required.
- `yaya plugin list` boots a transient registry, snapshots it, tears
  down. JSON mode emits `{"ok": true, "action": "plugin.list",
  "plugins": [...]}`; text mode renders a rich table.
- `yaya plugin install <source>` reuses the registry's
  `_validate_install_source` to reject shell metacharacters before
  any subprocess runs; under `--json` the command refuses to prompt
  and requires `--yes`, otherwise it emits `{"ok": false, "error":
  "confirmation_required", ...}`. `--dry-run` validates + confirms
  without calling pip.
- `yaya plugin remove <name>` delegates to `registry.remove`, which
  raises `ValueError` for bundled plugins; the CLI renders that as
  `ok=false` with a suggestion pointing at `yaya update`. `--yes` /
  `--dry-run` mirror install.
- Every command builds a result in `kernel/` or `core/` and renders
  through `yaya.cli.output.emit_ok` / `emit_error`; no raw
  `print` / `typer.echo`. Exit codes: `0` success, `1` functional
  error, `2` Typer argv error.

## Boundaries

### Allowed Changes
- src/yaya/cli/__init__.py
- src/yaya/cli/commands/hello.py
- src/yaya/cli/commands/serve.py
- src/yaya/cli/commands/plugin.py
- tests/cli/test_hello.py
- tests/cli/test_serve.py
- tests/cli/test_plugin.py
- specs/cli-kernel-commands.spec
- docs/wiki/log.md

### Forbidden
- src/yaya/kernel/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- GOAL.md
- docs/dev/plugin-protocol.md

## Completion Criteria

Scenario: yaya hello under --json returns ok=true with received=true
  Test:
    Package: yaya
    Filter: tests/cli/test_hello.py::test_hello_json_ok
  Level: integration
  Given a fresh kernel with no LLM configured
  When `yaya --json hello` is invoked
  Then the command exits 0 and stdout carries `{"ok": true, "action": "hello", "received": true}`

Scenario: Error path — yaya serve rejects --host
  Test:
    Package: yaya
    Filter: tests/cli/test_serve.py::test_serve_rejects_host_flag
  Level: unit
  Given the serve command registered on the CLI app
  When the user invokes `yaya serve --host 0.0.0.0`
  Then Typer exits with code 2 because no such flag is defined

Scenario: yaya plugin list under --json returns a valid plugins array
  Test:
    Package: yaya
    Filter: tests/cli/test_plugin.py::test_plugin_list_json
  Level: integration
  Given the four bundled seed plugins installed via entry points
  When `yaya --json plugin list` is invoked
  Then stdout carries `{"ok": true, "action": "plugin.list", "plugins": [...]}` listing every bundled plugin

Scenario: Error path — yaya plugin remove of a bundled plugin emits ok=false with suggestion
  Test:
    Package: yaya
    Filter: tests/cli/test_plugin.py::test_plugin_remove_bundled_rejected
  Level: integration
  Given a bundled plugin named strategy-react
  When `yaya --json plugin remove strategy-react --yes` is invoked
  Then the command exits 1 and stdout carries `{"ok": false, "error": "...bundled...", "suggestion": "...bundled..."}`

Scenario: Error path — yaya plugin install rejects shell metacharacters
  Test:
    Package: yaya
    Filter: tests/cli/test_plugin.py::test_plugin_install_shell_metachars_rejected
  Level: unit
  Given a source string containing shell metacharacters like `;`
  When `yaya --json plugin install "foo;rm -rf /" --yes` is invoked
  Then `_validate_install_source` raises ValueError before any subprocess is spawned
  And the CLI surfaces ok=false with a suggestion

Scenario: yaya serve shuts down cleanly when the shutdown event is set
  Test:
    Package: yaya
    Filter: tests/cli/test_serve.py::test_run_serve_clean_shutdown_via_event
  Level: integration
  Given `run_serve` is driven via its test-only shutdown_event hook
  When the event is set after boot
  Then stdout carries `{"ok": true, "action": "shutdown", "reason": "signal"}` and the task returns exit code 0

Scenario: yaya serve warns when no web adapter plugin is loaded
  Test:
    Package: yaya
    Filter: tests/cli/test_serve.py::test_run_serve_warns_when_no_adapter
  Level: integration
  Given the kernel boots without any plugin whose category is adapter and name starts with web
  When `run_serve` starts up
  Then a human-facing warning whose text contains "no web adapter" lands on stderr and the kernel continues running

Scenario: yaya plugin install under --json requires --yes
  Test:
    Package: yaya
    Filter: tests/cli/test_plugin.py::test_plugin_install_json_requires_yes
  Level: unit
  Given the user passes --json without --yes
  When `yaya --json plugin install some-pkg` is invoked
  Then the command exits 1 with ok=false and error=confirmation_required

## Out of Scope

- Real subprocess calls to pip in tests — subprocess is mocked.
- The web adapter plugin itself (issue #16).
- ctx.config plumbing for `--strategy` dispatch (tracked separately).
- `yaya plugin disable` (reserved for 0.5+).
