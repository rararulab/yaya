spec: task
name: "kernel-session-tape"
tags: [kernel, session, tape, persistence]
---

## Intent

The yaya kernel persists every session's event stream as a tape:
an append-only, anchor-aware log backed by `republic`'s
`AsyncTapeManager` / `TapeStore` primitives. Every session is one
tape; every relevant bus event is appended as a canonical
`TapeEntry`; anchors mark boundaries (session start, compaction,
fork). LLM context is a derived view via `TapeContext`, not a
mutable history. Fork / reset / compaction are cheap because they
never rewrite past entries. A kernel subscriber auto-persists bus
events per the table in `docs/dev/plugin-protocol.md#sessions-and-tape`.

## Decisions

- `src/yaya/kernel/session.py` owns the kernel-side wrapping:
  `SessionStore`, `Session`, `SessionInfo`, `MemoryTapeStore`,
  `tape_name_for`, `default_session_dir`. The file-backed store is
  a small in-house jsonl implementation so the persistence layer
  does not hard-depend on `bub`.
- `src/yaya/kernel/tape_context.py` ships the default
  entry → LLM-message selection (`default_tape_context`,
  `select_messages`, `after_last_anchor`). Mirrors the
  `vendor/bub` reference but reimplemented so yaya never imports
  `bub`.
- Five new public event kinds land in `events.py` per
  `PublicEventKind`: `session.started`, `session.handoff`,
  `session.reset`, `session.archived`, `session.forked`. Payload
  TypedDicts documented inline.
- `src/yaya/kernel/session_persister.py` owns the bus subscriber.
  Lesson-driven design points: writes NEVER go back through the
  bus; failures emit `plugin.error` with
  `source="kernel-session-persister"` (NOT `"kernel"` — recursion
  guard); `assistant.message.delta` / `llm.call.delta` /
  `session.*` are skipped (too chatty or circular); `persist=False`
  on any payload opts out; `session_id="kernel"` is skipped
  entirely so kernel-plane events do not pollute user tapes.
- `KernelContext.session` is a read-only property wired by the
  registry when the kernel boots with a `SessionStore`. Plugins
  may read it from `on_load` / `on_event`; the `ctx.publish` +
  auto-persister path is the canonical write route.
- Tape naming rule: `md5(workspace)[:16] + "__" + md5(session)[:16]`
  via `hashlib.md5(..., usedforsecurity=False)` — collision-tolerant
  routing id, not a security primitive.
- Archive output lives under `<tapes_dir>/.archive/<tape>.jsonl.<stamp>.bak`.
  `tapes_dir` defaults to `${YAYA_STATE_DIR:-${XDG_STATE_HOME:-~/.local/state}}/yaya/tapes/`.
- `Session.fork(child_id)` is a thin overlay: reads see
  parent-then-child entries; writes land on an in-memory child
  store; `child.reset()` never mutates the parent. Enough for
  subagent-style branches; persistent children open a new session
  with a different id.
- CLI: `yaya session list|show|resume|archive`. `yaya serve`
  accepts `--resume <id>` as a forward-compat surface.
- Config keys: `session.store: "file" | "memory"`, `session.dir:
  Path | None`, `session.default_id: str | None` live on
  `KernelConfig.session`.

## Boundaries

### Allowed Changes
- pyproject.toml
- uv.lock
- src/yaya/kernel/session.py
- src/yaya/kernel/session_persister.py
- src/yaya/kernel/tape_context.py
- src/yaya/kernel/events.py
- src/yaya/kernel/config.py
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/cli/__init__.py
- src/yaya/cli/commands/session.py
- src/yaya/cli/commands/serve.py
- docs/dev/plugin-protocol.md
- specs/kernel-session-tape.spec
- tests/kernel/test_session.py
- tests/kernel/test_session_bus.py
- tests/cli/test_session.py
- tests/bdd/features/kernel-session-tape.feature
- tests/bdd/test_kernel_session.py

### Forbidden
- src/yaya/plugins/
- GOAL.md
- src/yaya/kernel/approval.py
- src/yaya/kernel/loop.py
- src/yaya/kernel/bus.py
- src/yaya/kernel/tool.py

## Completion Criteria

Scenario: AC-01 — fresh session seeds a session start anchor
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_open_seeds_session_start_anchor
  Level: unit
  Given no tape exists for workspace W and session S
  When the session store opens the session
  Then the tape has exactly one anchor named session slash start
  And the anchor state includes owner human and workspace equal to the path

Scenario: AC-02 — bus events are persisted canonically
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_bus.py::test_user_and_assistant_events_round_trip
  Level: integration
  Given an open session and a running bus persister
  When a user message received event with content hi is emitted
  And an assistant message done event with content hello is emitted
  Then the tape contains a message entry role user content hi
  And a message entry role assistant content hello

Scenario: AC-03 — streaming deltas are not persisted
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_bus.py::test_assistant_delta_is_not_persisted
  Level: unit
  Given an open session and a running bus persister
  When ten assistant message delta events are emitted
  Then no new tape entries land on the tape beyond the bootstrap anchor

Scenario: AC-04 — persist false opts an event out
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_bus.py::test_persist_false_opts_out
  Level: unit
  Given an open session and a running bus persister
  When a user message received event is emitted with payload persist false
  Then the tape contains no new message entry for that event

Scenario: AC-05 — archive plus reset round trip
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_reset_archives_then_clears
  Level: integration
  Given a session with ten tape entries
  When reset with archive true is called
  Then a jsonl archive file exists under tapes archive
  And the tape has exactly one anchor named session slash start

Scenario: AC-06 — workspace scoped naming isolates cross workspace tapes
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_workspace_scoped_tape_names
  Level: unit
  Given two workspaces with the same session id default
  When both sessions are opened
  Then their tape names differ
  And list sessions for each workspace returns one row

Scenario: AC-07 — fork overlay isolates writes
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_fork_isolates_child_writes
  Level: unit
  Given a parent session with five entries
  When the parent forks a child subagent
  And the child appends three entries
  Then the parent tape still has five entries
  And the child context sees eight entries

Scenario: AC-08 — post compaction context starts after the last anchor
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_after_last_anchor_helper
  Level: unit
  Given a session with a compaction anchor followed by two new messages
  When after last anchor is called
  Then only the two post anchor messages are returned

Scenario: AC-09 — tape write failure does not kill the session
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_bus.py::test_tape_failure_emits_plugin_error
  Level: unit
  Given a persister whose session store raises on append
  When a user message received event is emitted
  Then a plugin error event is observed with source kernel session persister
  And the bus keeps routing subsequent events

Scenario: AC-10 — kernel context exposes the active session to plugins
  Test:
    Package: yaya
    Filter: tests/kernel/test_session.py::test_kernel_context_exposes_session
  Level: unit
  Given a kernel context wired with an open session
  Then the context session property returns that session
  And the property cannot be overwritten

## Out of Scope

- Remote / multi-machine tape synchronisation.
- Tape encryption at rest.
- Compaction policy (handled in #29 once the tape substrate is live).
- Automatic eviction of the persister's session cache — adapter
  lifecycle will drive that when the session-scope work lands.
