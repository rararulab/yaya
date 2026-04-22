Feature: Kernel health and the doctor command

  These scenarios mirror specs/kernel-health.spec and keep the
  per-plugin health_check contract plus yaya doctor's exit-code
  and rendering rules executable.

  Scenario: AC-01 — doctor renders a per-plugin table
    Given a fresh kernel with every bundled plugin loaded
    When the doctor command runs in json mode
    Then each bundled plugin appears in the plugins list with a status field

  Scenario: AC-02 — degraded plugins do not fail the run
    Given every plugin reports degraded
    When the doctor command exits
    Then the exit code is zero
    And the json ok field is true

  Scenario: AC-03 — a failed plugin fails the run
    Given one bundled plugin reports failed
    When the doctor command exits
    Then the exit code is one
    And the json error field is plugin_failed

  Scenario: AC-04 — one hung plugin does not block the run
    Given a plugin whose health check never returns
    When the doctor helper invokes it with a tight timeout
    Then the reported status is degraded with a timed out summary

  Scenario: AC-05 — missing health_check synthesises an ok default
    Given a plugin without a health check method
    When the doctor helper invokes the default synthesiser
    Then the reported status is ok with the default summary

  Scenario: AC-06 — bus round-trip failure fails the run
    Given the event bus drops the synthetic event
    When the doctor command exits
    Then the exit code is one
    And the json error field is event_bus_unresponsive
