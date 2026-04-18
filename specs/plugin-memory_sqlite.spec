spec: task
name: "plugin-memory_sqlite"
tags: [plugin, memory]
---

## Intent

The SQLite memory plugin keeps conversation entries in a stdlib
sqlite table owned by the plugin and answers `memory.query` by
returning the top-k rows whose text matches a SQL LIKE pattern,
ordered by recency. Blocking sqlite calls run through
`asyncio.to_thread` so slow IO cannot stall the event bus.

## Decisions

- Subscribes to `memory.query` and `memory.write`; emits
  `memory.result` carrying `hits` and echoing the originating
  event's id back as `request_id`.
- Schema is `memory(id TEXT PRIMARY KEY, text TEXT NOT NULL, meta
  TEXT, ts REAL)`; `meta` is JSON-serialized so nested dicts round-
  trip.
- `memory.write` without an `id` assigns a fresh `uuid4().hex`; a
  duplicate id raises `sqlite3.IntegrityError`, which the handler
  catches and surfaces as a WARNING log (duplicates are a data
  event, not a plugin bug — no exception escapes).
- Query defaults to `k=5` when the payload omits or mistypes it; a
  non-positive `k` is coerced to the default so the LIKE scan never
  runs with a zero-or-negative limit.
- The sqlite connection opens in `on_load` against a `memory.db`
  file under the plugin state directory and closes idempotently in
  `on_unload`, so a hot-reload never double-opens the database and
  the round-trip scenario can observe a persisted write on disk
  across the open-close boundary.

## Boundaries

### Allowed Changes
- src/yaya/plugins/memory_sqlite/__init__.py
- src/yaya/plugins/memory_sqlite/plugin.py
- src/yaya/plugins/memory_sqlite/AGENT.md
- tests/plugins/memory_sqlite/__init__.py
- tests/plugins/memory_sqlite/test_memory_sqlite.py
- specs/plugin-memory_sqlite.spec

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/tool_bash/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Round-trip write then query returns the persisted entry as a hit
  Test:
    Package: yaya
    Filter: tests/plugins/memory_sqlite/test_memory_sqlite.py::test_write_then_query_roundtrip
  Level: unit
  Given a loaded memory-sqlite plugin with an empty database
  When a memory.write event is published followed by a matching memory.query
  Then a memory.result event is emitted with one hit whose id and text match the written entry
  And the response echoes the originating request id

Scenario: memory.write without id assigns a fresh uuid4 hex
  Test:
    Package: yaya
    Filter: tests/plugins/memory_sqlite/test_memory_sqlite.py::test_write_without_id_generates_uuid
  Level: unit
  Given a loaded memory-sqlite plugin
  When a memory.write event is published whose entry has no id field
  Then a uuid4 hex id is persisted and appears in the next memory.result hit list

Scenario: Error path — duplicate id write logs warning and does not raise
  Test:
    Package: yaya
    Filter: tests/plugins/memory_sqlite/test_memory_sqlite.py::test_duplicate_id_logs_warning
  Level: unit
  Given a loaded memory-sqlite plugin with one entry already persisted
  When a second memory.write event is published reusing the same id
  Then a WARNING log entry is recorded naming the duplicate id
  And no exception escapes the handler

Scenario: Empty query tails most recent entries ordered by recency
  Test:
    Package: yaya
    Filter: tests/plugins/memory_sqlite/test_memory_sqlite.py::test_empty_query_tails_recent
  Level: unit
  Given a loaded memory-sqlite plugin with three entries persisted in order
  When a memory.query event is published with an empty query string and k equal to 2
  Then a memory.result event is emitted whose hits are the two most recent entries ordered by ts desc

## Out of Scope

- Vector / embedding search (future `memory_vec` plugin).
- Long-term vs short-term routing by session age.
- Concurrent connections across sessions (sqlite3 thread-safety).
