Feature: Kernel plugin registry — discovery, lifecycle, and failure isolation

  The registry discovers plugins via setuptools entry points in the
  ``yaya.plugins.v1`` group, drives their ``on_load``/``on_event``/
  ``on_unload`` lifecycle, isolates repeat-offender plugins past a
  configurable failure threshold, and exposes snapshot, install, and
  remove surfaces. Bundled and third-party plugins travel the same
  code path; "bundled" only influences deterministic load order.

  Scenarios mirror specs/kernel-registry.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: Entry-point discovery loads a plugin and emits plugin.loaded
    Given a Plugin object exposed via a yaya.plugins.v1 entry point
    When the registry is started and entry-point discovery runs
    Then the plugin's on_load is called exactly once
    And a plugin.loaded event is emitted carrying its name, version, and category

  Scenario: Bundled plugin uses the same load code path as third-party
    Given one bundled and one third-party plugin registered under the same entry-point group
    When the registry is started
    Then both plugins run through the identical _load_entry_point code path with no behavioral branch
    And the bundled plugin appears first in snapshot() as the deterministic load-order tie-breaker

  Scenario: Error path — repeated plugin.error failures past threshold unload the plugin
    Given a plugin whose on_event raises every time
    And a failure threshold default of 3 consecutive plugin.error events
    When three subscribed events are delivered to that plugin
    Then the registry unloads the plugin and emits plugin.removed
    And the plugin's on_unload is called exactly once
    And its status in snapshot() becomes "failed"

  Scenario: Consecutive failure counter resets on a successful on_event
    Given a plugin registered with the default consecutive failure threshold
    And two plugin.error events already attributed to that loaded plugin
    When a subsequent on_event invocation succeeds
    Then the consecutive failure counter resets to zero
    And the plugin stays loaded despite a later isolated plugin.error

  Scenario: snapshot returns one entry per registered plugin with name, version, category, status
    Given two plugins that load successfully and one that fails on_load
    When registry.snapshot() is called
    Then three entries are returned carrying name, version, category, status fields in first-seen load order
    And the failing plugin's status is "failed"

  Scenario: install shells to uv pip via subprocess_exec and re-runs discovery
    Given an uv binary available on PATH
    When registry.install("yaya-tool-bash") is called
    Then asyncio.create_subprocess_exec is invoked with "uv pip install yaya-tool-bash"
    And entry-point discovery re-runs so the freshly installed plugin comes online

  Scenario: Error path — install source validation rejects shell metacharacters
    Given a source string containing shell metacharacters like ";"
    When registry.install(source) is called
    Then ValueError is raised by source validation before any subprocess is spawned

  Scenario: Error path — remove refuses to uninstall a bundled plugin
    Given a bundled plugin whose bundled membership is re-derived from entry-point metadata
    When registry.remove("<bundled-name>") is called
    Then ValueError is raised referencing "bundled" at the enforcement point

  Scenario: stop runs on_unload in reverse load order and emits kernel.shutdown
    Given two loaded plugins registered in order A then B
    When registry.stop() is called
    Then on_unload is invoked on B before A in reverse load order
    And a kernel.shutdown event is emitted once every loaded plugin has unloaded

  Scenario: Concurrent plugin.error events past threshold trigger a single unload task
    Given a loaded plugin whose consecutive failure counter is one below threshold
    When several concurrent plugin.error events for that plugin arrive in the same tick
    Then the registry claims the transient unloading status synchronously and schedules exactly one unload task
    And rival handlers observe the flip and short-circuit
