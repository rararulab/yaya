Feature: Kernel approval runtime

  These scenarios mirror specs/kernel-approval.spec and keep the
  human-in-the-loop approval path executable: approve, reject, timeout,
  session caching, kernel-session routing, and shutdown cancellation.

  Scenario: AC-01 — user approves, tool runs once
    Given a Tool subclass with requires approval true
    And an adapter that answers approval request with approve
    When a tool call request is emitted for the tool
    Then the tool run method is invoked exactly once
    And a tool call result event is emitted with ok true

  Scenario: AC-02 — user rejects, tool is blocked
    Given a Tool subclass with requires approval true
    And an adapter that answers approval request with reject and feedback no thanks
    When a tool call request is emitted for the tool
    Then the tool run method is not invoked
    And a tool error event is emitted with kind rejected whose brief mentions no thanks

  Scenario: AC-03 — approve for session short-circuits future prompts
    Given an adapter that answers the first approval with approve for session
    When two identical tool call requests are published on the same session
    Then exactly one approval request event is observed by the adapter
    And both tool call result events carry ok true

  Scenario: AC-04 — 60s timeout cancels the approval
    Given an approval runtime with a very short timeout
    And no adapter subscribed to approval request
    When the runtime request method is awaited
    Then an approval cancelled event is emitted with reason timeout
    And an approval cancelled error is raised

  Scenario: AC-05 — approval events route on the kernel session to avoid deadlock
    Given an adapter subscribed to approval request
    When the runtime issues a request from inside a tool call session worker
    Then the approval request envelope carries session id kernel
    And the resolve path does not deadlock the originating session worker

  Scenario: AC-06 — shutdown cancels pending approvals
    Given a pending approval request
    When the approval runtime stop method is called
    Then the awaiting caller observes an approval cancelled error with reason shutdown
