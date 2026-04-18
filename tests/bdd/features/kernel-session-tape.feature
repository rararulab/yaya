Feature: Kernel session + tape store

  The kernel persists every session's bus events as an append-only
  tape (republic primitives). LLM context is a derived view via
  TapeContext; fork, reset, and compaction never rewrite past
  entries. A kernel subscriber auto-persists bus events per the
  table in docs/dev/plugin-protocol.md#sessions-and-tape.

  Scenarios mirror specs/kernel-session-tape.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: AC-01 — fresh session seeds a session start anchor
    Given no tape exists for workspace W and session S
    When the session store opens the session
    Then the tape has exactly one anchor named session slash start
    And the anchor state includes owner human and workspace equal to the path

  Scenario: AC-02 — bus events are persisted canonically
    Given an open session and a running bus persister
    When a user message received event with content hi is emitted
    And an assistant message done event with content hello is emitted
    Then the tape contains a message entry role user content hi
    And a message entry role assistant content hello

  Scenario: AC-03 — streaming deltas are not persisted
    Given an open session and a running bus persister
    When ten assistant message delta events are emitted
    Then no new tape entries land on the tape beyond the bootstrap anchor

  Scenario: AC-04 — persist false opts an event out
    Given an open session and a running bus persister
    When a user message received event is emitted with payload persist false
    Then the tape contains no new message entry for that event

  Scenario: AC-05 — archive plus reset round trip
    Given a session with ten tape entries
    When reset with archive true is called
    Then a jsonl archive file exists under tapes archive
    And the tape has exactly one anchor named session slash start

  Scenario: AC-06 — workspace scoped naming isolates cross workspace tapes
    Given two workspaces with the same session id default
    When both sessions are opened
    Then their tape names differ
    And list sessions for each workspace returns one row

  Scenario: AC-07 — fork overlay isolates writes
    Given a parent session with five entries
    When the parent forks a child subagent
    And the child appends three entries
    Then the parent tape still has five entries
    And the child context sees eight entries

  Scenario: AC-08 — post compaction context starts after the last anchor
    Given a session with a compaction anchor followed by two new messages
    When after last anchor is called
    Then only the two post anchor messages are returned

  Scenario: AC-09 — tape write failure does not kill the session
    Given a persister whose session store raises on append
    When a user message received event is emitted
    Then a plugin error event is observed with source kernel session persister
    And the bus keeps routing subsequent events

  Scenario: AC-10 — kernel context exposes the active session to plugins
    Given a kernel context wired with an open session
    Then the context session property returns that session
    And the property cannot be overwritten
