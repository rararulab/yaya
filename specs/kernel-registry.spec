spec: task
name: "kernel-registry"
tags: [kernel, registry, plugins]
---

## Intent

The kernel's plugin registry is the layer that turns an installed
Python package into a live subscriber on yaya's event bus. It reads
setuptools entry points in the `yaya.plugins.v1` group, instantiates
each declared `Plugin` object, wires it into the bus, drives its
`on_load` / `on_event` / `on_unload` lifecycle, and isolates
repeat-offender plugins by unloading them and emitting
`plugin.removed` after a configurable failure threshold. Bundled and
third-party plugins go through the same code path; "bundled" is only
a deterministic load-order tie-breaker, never a behavioral branch.

## Decisions

- `PluginRegistry` discovers entry points in the `yaya.plugins.v1`
  group, instantiates each `Plugin` object, calls its `on_load` once,
  and emits a `plugin.loaded` event with the plugin's name, version,
  and category on successful load.
- Bundled and third-party plugins run through the identical
  `_load_entry_point` code path; bundled plugins sort first in
  `snapshot()` for a deterministic load-order tie-breaker but have no
  behavioral branch.
- Failure threshold default = 3 consecutive `plugin.error` events
  attributed to a loaded plugin triggers unload with
  `reason="threshold"`, emits `plugin.removed`, and calls the
  plugin's `on_unload` exactly once; status in `snapshot()` becomes
  `"failed"`. A successful `on_event` invocation resets the
  consecutive failure counter to zero.
- `snapshot()` returns one entry per registered plugin carrying
  `name`, `version`, `category`, and `status` fields in first-seen
  load order — including plugins that failed to load.
- `install(source)` shells out to `uv pip install` via
  `asyncio.create_subprocess_exec` (never `shell=True`) and re-runs
  entry-point discovery so a freshly installed plugin comes online
  without a kernel restart.
- `install` source validation rejects sources that do not match an
  accepted shape (PyPI name/spec, absolute path, `file://` or
  `https://` URL) with `ValueError` before any subprocess is spawned.
  Shell-injection safety comes from `create_subprocess_exec` (no
  shell), not from character filtering — unsupported URL schemes
  (e.g. `git+ssh://`), relative paths, plain `http://`, empty input,
  and embedded `\n`/`\r` are all rejected.
- `remove(name)` raises `ValueError` referencing "bundled" when the
  named plugin is bundled, re-deriving bundled membership from
  entry-point metadata at the enforcement point.
- `stop()` runs `on_unload` for every loaded plugin in reverse load
  order (last loaded unloads first) and emits `kernel.shutdown`.
- Concurrent `plugin.error` events past threshold trigger a single
  unload task: the transient `unloading` status is claimed
  synchronously before the unload task is scheduled, so rival
  handlers observe the flip and short-circuit.
- Registry startup installs the kernel v1 tool dispatcher before
  `kernel.ready`, so plugins that register `Tool` subclasses during
  `on_load` can service `tool.call.request` events carrying
  `schema_version="v1"`.

## Boundaries

### Allowed Changes
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- tests/kernel/test_registry.py
- tests/bdd/features/kernel-registry.feature
- tests/bdd/test_kernel_registry.py
- specs/kernel-registry.spec

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Entry-point discovery loads a plugin and emits plugin.loaded
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_entry_point_discovery_loads_plugin
  Level: unit
  Given a Plugin object exposed via a yaya.plugins.v1 entry point
  When the registry is started and entry-point discovery runs
  Then the plugin's on_load is called exactly once
  And a plugin.loaded event is emitted carrying its name, version, and category

Scenario: Bundled plugin uses the same load code path as third-party
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_bundled_plugin_uses_same_load_path
  Level: unit
  Given one bundled and one third-party plugin registered under the same entry-point group
  When the registry is started
  Then both plugins run through the identical _load_entry_point code path with no behavioral branch
  And the bundled plugin appears first in snapshot() as the deterministic load-order tie-breaker

Scenario: Error path — repeated plugin.error failures past threshold unload the plugin
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_repeated_failures_unload_plugin
  Level: unit
  Given a plugin whose on_event raises every time
  And a failure threshold default of 3 consecutive plugin.error events
  When three subscribed events are delivered to that plugin
  Then the registry unloads the plugin and emits plugin.removed
  And the plugin's on_unload is called exactly once
  And its status in snapshot() becomes "failed"

Scenario: Consecutive failure counter resets on a successful on_event
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_error_counter_resets_on_successful_event
  Level: unit
  Given a plugin registered with the default consecutive failure threshold
  And two plugin.error events already attributed to that loaded plugin
  When a subsequent on_event invocation succeeds
  Then the consecutive failure counter resets to zero
  And the plugin stays loaded despite a later isolated plugin.error

Scenario: snapshot returns one entry per registered plugin with name, version, category, status
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_snapshot_lists_every_plugin_with_status
  Level: unit
  Given two plugins that load successfully and one that fails on_load
  When registry.snapshot() is called
  Then three entries are returned carrying name, version, category, status fields in first-seen load order
  And the failing plugin's status is "failed"

Scenario: Registry startup installs the v1 tool dispatcher
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_registry_installs_v1_tool_dispatcher
  Level: unit
  Given a plugin that registers a v1 Tool during on_load
  When registry is started and a v1 tool.call.request is published
  Then a tool.call.result event is emitted by the kernel dispatcher

Scenario: install shells to uv pip via subprocess_exec and re-runs discovery
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_install_invokes_subprocess_and_refreshes
  Level: unit
  Given an uv binary available on PATH
  When registry.install("yaya-tool-bash") is called
  Then asyncio.create_subprocess_exec is invoked with "uv pip install yaya-tool-bash"
  And entry-point discovery re-runs so the freshly installed plugin comes online

Scenario: Error path — install source validation rejects unsupported scheme
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_validate_install_source_rejects_hazards
  Level: unit
  Given a source string with an unsupported URL scheme like "git+ssh"
  When registry.install(source) is called
  Then ValueError is raised by source validation before any subprocess is spawned

Scenario: Error path — remove refuses to uninstall a bundled plugin
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_remove_bundled_plugin_raises
  Level: unit
  Given a bundled plugin whose bundled membership is re-derived from entry-point metadata
  When registry.remove("<bundled-name>") is called
  Then ValueError is raised referencing "bundled" at the enforcement point

Scenario: stop runs on_unload in reverse load order and emits kernel.shutdown
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_stop_runs_on_unload_in_reverse_order
  Level: unit
  Given two loaded plugins registered in order A then B
  When registry.stop() is called
  Then on_unload is invoked on B before A in reverse load order
  And a kernel.shutdown event is emitted once every loaded plugin has unloaded

Scenario: Concurrent plugin.error events past threshold trigger a single unload task
  Test:
    Package: yaya
    Filter: tests/kernel/test_registry.py::test_concurrent_errors_trigger_single_unload
  Level: unit
  Given a loaded plugin whose consecutive failure counter is one below threshold
  When several concurrent plugin.error events for that plugin arrive in the same tick
  Then the registry claims the transient unloading status synchronously and schedules exactly one unload task
  And rival handlers observe the flip and short-circuit

## Out of Scope

- Hot-reload of an already-loaded plugin on a new version (remove +
  install today).
- Per-plugin config loading (tracked separately).
- `yaya plugin list / install / remove` CLI adapter (ships in a
  separate PR).
- 2.0 sandbox / capability restrictions.
