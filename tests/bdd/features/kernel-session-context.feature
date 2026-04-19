Feature: Kernel SessionContext — multi-connection fanout, turn ordering, reconnect replay

  Multiple connections (browser tabs, TUI clients, future adapters)
  attach to the same yaya Session and observe a consistent event
  stream. Reconnects replay missed entries from the tape; live
  events arriving mid-replay buffer so nothing is dropped or
  duplicated. The registry is bounded and a heartbeat reap task
  drops silent clients.

  Scenarios mirror specs/kernel-session-context.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: AC-01 — fanout reaches every attached connection
    Given a session context with two attached connections A and B
    When the manager fans out one event on the session
    Then both A and B receive the event exactly once

  Scenario: AC-02 — event ordering is identical across connections
    Given a session context with two attached connections
    When one hundred events are fanned out
    Then both connections observe the same sequence

  Scenario: AC-03 — reconnect replay emits missed entries then a done sentinel
    Given a session tape with several entries
    When a connection attaches with since entry set to the id of an earlier entry
    Then the connection receives session replay entry events for every later entry
    And a single session replay done event closes the replay

  Scenario: AC-04 — connection registry is bounded
    Given a session context configured with max connections equal to two
    When a third connection attempts to attach
    Then a connection limit error is raised naming the cap

  Scenario: AC-05 — heartbeat reap drops silent connections
    Given a session context with a short heartbeat timeout
    And a connection whose last seen is older than the timeout
    When the reap sweep runs
    Then the stale connection is detached with reason timeout
    And a session context detached event is emitted

  Scenario: AC-06 — lifecycle events route on the kernel session
    Given a bus subscriber listening to session context events on session id kernel
    When a connection attaches to a context for session S
    Then the subscriber observes the attached event
    And the envelope session id is kernel

  Scenario: AC-07 — send failure detaches offender without blocking fanout
    Given a session context with one healthy connection and one connection whose send always raises
    When the manager fans out one event
    Then the healthy connection receives the event
    And the raising connection is detached with reason send failed

  Scenario: AC-08 — close detaches every connection with shutdown reason
    Given a session context with two attached connections
    When close is awaited
    Then both connections receive a session context detached event
    And the reason is shutdown

  Scenario: AC-09 — manager routes bus events to the matching context
    Given a session manager installed with a user message received subscription
    And a connection attached for session default
    When a user message received event is published on session default
    Then the connection receives the same event

  Scenario: AC-10 — live event arriving during replay buffers behind the replay lock
    Given a session with several tape entries
    When a connection attaches with since entry zero
    And a live event is fanned out while the attach is still running
    Then the connection observes every replay entry before the live event
