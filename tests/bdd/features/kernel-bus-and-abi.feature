Feature: Kernel EventBus delivery and ABI contracts

  The bus fans exact-kind events to subscribers, isolates handler
  failures, respects per-session FIFO ordering, carries a closed
  public catalog with an open extension namespace, and lets handlers
  publish follow-up events on the same session without deadlocking.

  Scenarios mirror specs/kernel-bus-and-abi.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: Bus delivers event to subscribers registered for the kind
    Given a running EventBus
    And a subscriber registered for "user.message.received"
    When a "user.message.received" event is published
    Then the subscriber receives the event with envelope fields populated

  Scenario: Error path — raising subscriber is isolated and emits plugin.error
    Given a running EventBus
    And a subscriber that raises on receipt
    And a second healthy subscriber for the same kind
    When the event is published
    Then the healthy subscriber still receives the event
    And a synthetic "plugin.error" event is emitted by the bus

  Scenario: Event envelope carries id kind session_id ts source payload fields
    Given the events module
    When new_event is called with a known public kind
    Then the returned Event envelope has id, ts, source, session_id, kind, and payload fields

  Scenario: Extension namespace routes arbitrary payload through the bus
    Given a running EventBus
    And a subscriber for "x.foo.bar" in the extension namespace
    When an "x.foo.bar" event is published with an arbitrary payload
    Then the subscriber receives it unchanged without type checks

  Scenario: Error path — closed public catalog rejects unknown kinds
    Given the events module with its closed public catalog
    When new_event is called with unknown public kind "nonsense.unknown"
    Then ValueError is raised referencing the closed public catalog

  Scenario: FIFO per-session handler publishes on the same session without deadlock
    Given a running EventBus with a single drain worker per session
    And a subscriber that publishes a follow-up event on the same session
    When the first event is delivered under the 30 s per-subscriber timeout
    Then the follow-up event is enqueued and delivered in FIFO order without deadlocking
