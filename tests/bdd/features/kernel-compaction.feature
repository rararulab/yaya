Feature: Kernel conversation compaction

  The kernel ships a pluggable Summarizer, a char-based token
  estimator, a manual Session.compact helper, and an auto-trigger
  CompactionManager driven by a configurable token threshold.
  Compaction appends a kind equal to compaction anchor carrying the
  summary; the default tape context injects that summary as a role
  system message so the LLM sees a compressed history rather than
  the full log.

  Scenarios mirror specs/kernel-compaction.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: AC-01 — manual compact appends a compaction anchor
    Given a session with five user messages since the last anchor
    When session compact runs with a fake summariser
    Then the tape has a new anchor with state kind equal to compaction
    And the anchor state carries the summary string

  Scenario: AC-02 — empty post-anchor window is a no-op
    Given a fresh session with only the bootstrap anchor
    When session compact runs with a fake summariser
    Then the summary string is empty
    And the tape still has only the bootstrap anchor

  Scenario: AC-03 — estimator is deterministic
    Given a fixed list of tape entries
    When estimate text tokens runs twice over the same entries
    Then both calls return the same positive integer

  Scenario: AC-04 — default context injects the summary as system
    Given a session with two pre compaction messages then a compaction anchor
    When default context is rendered from the tape
    Then the returned messages start with a role system summary message

  Scenario: AC-05 — summariser failure is translated to a failed event
    Given a session with one pre compaction message
    When session compact runs with an exploding summariser
    Then a session compaction failed event is emitted
    And no compaction anchor is appended

  Scenario: AC-06 — auto manager triggers once past the threshold
    Given a running compaction manager with a low threshold
    When a user message received event pushes the tape past threshold
    Then the summariser is invoked at least once

  Scenario: AC-07 — single in flight guard
    Given a compaction manager with a slow summariser
    When three user message received events arrive back to back
    Then the summariser is invoked exactly once while the first call is pending

  Scenario: AC-08 — fork compaction does not mutate the parent tape
    Given a parent session with three user messages
    When the parent forks a child and the child compacts
    Then the parent tape entry count is unchanged

  Scenario: AC-09 — yaya session compact cli happy path
    Given a seeded session with a couple of messages
    When yaya json session compact default runs
    Then the exit code is zero
    And the json output has action equal to session dot compact
