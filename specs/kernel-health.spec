spec: task
name: "kernel-health"
tags: [kernel, cli, plugins, health, doctor]
---

## Intent

yaya ships a `yaya doctor` CLI command — a rename of the pre-0.1
`yaya hello` smoke test — that boots the kernel, round-trips one
synthetic `user.message.received`, then invokes an optional
`health_check(ctx)` method on every loaded plugin and renders the
aggregated status. Each bundled plugin gains a meaningful, fast
(<500 ms) self-check — `llm-openai` reports whether any instance
has a resolvable API key, `memory-sqlite` probes `SELECT 1`,
`tool-bash` checks `$PATH`, and so on. The doctor command is the
agent's first-run sanity gate: a single command tells the operator
which plugins are ready, which are degraded (missing config), and
which have failed.

## Decisions

- `src/yaya/kernel/plugin.py` adds `HealthStatus`
  (`Literal["ok","degraded","failed"]`), `HealthCheck`, and
  `HealthReport` pydantic models. `health_check(ctx)` is deliberately
  **not** declared on the `runtime_checkable` `Plugin` Protocol —
  adding it would break `isinstance(obj, Plugin)` for every
  third-party plugin that does not implement it. The doctor command
  inspects with `hasattr` and synthesises a default
  `HealthReport(status="ok", summary="no checks registered")` on miss.
- `src/yaya/cli/commands/doctor.py` replaces `hello.py` wholesale.
  The file's exit-code contract is: `0` when the round-trip succeeded
  and no plugin reports `failed`; `1` otherwise. `degraded` is
  explicitly an exit-0 state because the install-day OpenAI-without-
  key case is the common path and we must not scare new users.
- `asyncio.wait_for(plugin.health_check(ctx), timeout=timeout_s)`
  per plugin, default 3 s, overridable via `--timeout`. A timeout
  becomes `status="degraded"` with `summary="check timed out after Ns"`
  so one hung plugin cannot block the rest of the doctor run.
  Raised exceptions become `status="failed"` with the exception text.
- `PluginRegistry.context_for(name)` is a new public accessor that
  returns the stored `KernelContext` for a loaded plugin — the doctor
  command needs the SAME context the plugin saw during `on_load` so
  `ctx.providers` reads through the registry's config store, not a
  freshly-built one.
- Each bundled plugin's `health_check` inspects only in-memory state
  populated during `on_load`. No network to third parties, no LLM
  calls, no new subprocess spawns. `tool-bash` runs `shutil.which`;
  `memory-sqlite` runs a single `SELECT 1` on the already-open
  connection through its existing DB executor; `web` stats the
  `importlib.resources.files(...)` path. The whole doctor run
  completes in the sub-second range on a dev laptop.
- JSON output shape: `{action, ok, roundtrip: {ok, latency_ms},
  plugins: [{name, category, status, summary, details}], version}`.
  Follows the agent-friendly-cli `ok`/`error`/`suggestion` contract
  via `emit_ok` / `emit_error`.
- No new public event kinds. `yaya doctor` makes direct Python calls
  on the registry — the event bus catalogue stays closed.

## Boundaries

### Allowed Changes

- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/__init__.py
- src/yaya/cli/commands/doctor.py
- src/yaya/cli/commands/AGENT.md
- src/yaya/plugins/llm_echo/plugin.py
- src/yaya/plugins/llm_openai/plugin.py
- src/yaya/plugins/tool_bash/plugin.py
- src/yaya/plugins/mcp_bridge/plugin.py
- src/yaya/plugins/memory_sqlite/plugin.py
- src/yaya/plugins/strategy_react/plugin.py
- src/yaya/plugins/web/plugin.py
- src/yaya/plugins/agent_tool/plugin.py
- GOAL.md
- docs/dev/plugin-protocol.md
- specs/kernel-health.spec
- tests/cli/test_doctor.py
- tests/cli/__snapshots__/test_help_snapshot.ambr
- tests/kernel/test_plugin.py
- tests/plugins/test_health_checks.py
- tests/bdd/features/kernel-health.feature
- tests/bdd/test_kernel_health.py

### Forbidden

- src/yaya/kernel/events.py (no new public event kinds)
- src/yaya/kernel/bus.py
- pyproject.toml (no new runtime deps)

## Completion Criteria

Scenario: AC-01 — doctor renders a per-plugin table
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_json_ok
  Level: integration
  Given a fresh kernel with every bundled plugin loaded
  When the doctor command runs in json mode
  Then each bundled plugin appears in the plugins list with a status field

Scenario: AC-02 — degraded plugins do not fail the run
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_degraded_is_exit_zero
  Level: integration
  Given every plugin reports degraded
  When the doctor command exits
  Then the exit code is zero
  And the json ok field is true

Scenario: AC-03 — a failed plugin fails the run
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_failed_plugin_exits_one
  Level: integration
  Given one bundled plugin reports failed
  When the doctor command exits
  Then the exit code is one
  And the json error field is plugin_failed

Scenario: AC-04 — one hung plugin does not block the run
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_health_check_timeout_surfaces_degraded
  Level: unit
  Given a plugin whose health check never returns
  When the doctor helper invokes it with a tight timeout
  Then the reported status is degraded with a timed out summary

Scenario: AC-05 — missing health_check synthesises an ok default
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_default_when_no_health_check
  Level: unit
  Given a plugin without a health check method
  When the doctor helper invokes the default synthesiser
  Then the reported status is ok with the default summary

Scenario: AC-06 — bus round-trip failure fails the run
  Test:
    Package: yaya
    Filter: tests/cli/test_doctor.py::test_doctor_roundtrip_timeout_exits_one
  Level: integration
  Given the event bus drops the synthetic event
  When the doctor command exits
  Then the exit code is one
  And the json error field is event_bus_unresponsive

## Out of Scope

- Periodic / streaming health (`yaya doctor --watch`).
- Cross-plugin dependency graphs in the report.
- Real network probes of external services (OpenAI, MCP).
- Persistent health history or metrics export.
