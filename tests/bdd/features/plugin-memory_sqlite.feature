Feature: SQLite memory plugin

  The executable Gherkin mirror of specs/plugin-memory_sqlite.spec.

  Scenario: Round-trip write then query returns the persisted entry as a hit
    Given a loaded memory-sqlite plugin with an empty database
    When a memory.write event is published followed by a matching memory.query
    Then a memory.result event is emitted with one hit whose id and text match the written entry
    And the response echoes the originating request id

  Scenario: memory.write without id assigns a fresh uuid4 hex
    Given a loaded memory-sqlite plugin
    When a memory.write event is published whose entry has no id field
    Then a uuid4 hex id is persisted and appears in the next memory.result hit list

  Scenario: Error path — duplicate id write logs warning and does not raise
    Given a loaded memory-sqlite plugin with one entry already persisted
    When a second memory.write event is published reusing the same id
    Then a WARNING log entry is recorded naming the duplicate id
    And no exception escapes the handler

  Scenario: Empty query tails most recent entries ordered by recency
    Given a loaded memory-sqlite plugin with three entries persisted in order
    When a memory.query event is published with an empty query string and k equal to 2
    Then a memory.result event is emitted whose hits are the two most recent entries ordered by ts desc
