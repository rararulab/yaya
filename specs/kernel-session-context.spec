spec: task
name: "kernel-session-context"
tags: [kernel, session, multi-connection, fanout, reconnect]
---

## Intent

Multiple connections (browser tabs, TUI clients, future Telegram /
Slack adapters) attach to the same yaya `Session` and observe a
consistent event stream. Reconnects replay missed entries from the
tape with proper race handling so live events do not get dropped or
duplicated mid-replay. The connection registry is bounded; a
heartbeat reap loop drops silent clients after a configurable
timeout. Losing the `SessionContext` is non-fatal — the tape on
disk is the source of truth. GOAL.md caps scope at single-process
local-first through 1.0, so "multi-connection" means "many clients
in one yaya process", not cross-host sync.

## Decisions

- `src/yaya/kernel/session_context.py` owns the runtime wrapper:
  `Connection`, `SessionContext`, `SessionManager`,
  `install_session_manager`, plus the closed `DetachReason`
  literal and `ConnectionLimitError` subclass of `YayaError`.
- `Connection` holds `id`, `adapter_id`, `send` callback,
  `attached_at`, `last_seen`, and a per-connection `asyncio.Lock`
  used to serialise replay against live fanout (race guard).
- `SessionContext` caps the connection registry at
  `DEFAULT_MAX_CONNECTIONS=64` (lesson #6 — bounded dicts). A
  heartbeat reap task runs every 5 s and detaches connections
  whose `last_seen` is older than `DEFAULT_HEARTBEAT_TIMEOUT_S=60`.
  `close()` cancels the reap task and detaches every remaining
  connection with `reason="shutdown"` before clearing the registry.
- Five new public event kinds land in `events.py`:
  `session.context.attached`, `session.context.detached`,
  `session.context.evicted`, `session.replay.entry`,
  `session.replay.done`. All route on `session_id="kernel"`
  (lesson #2) so they never enqueue behind the drain worker that
  is fanning them out.
- `SessionContext.fanout` snapshots the registry, acquires each
  connection's per-conn lock, and calls its `send`. Send failures
  translate to a quiet `detach(reason="send_failed")` per
  lesson #29 — the surviving connections still receive the event.
- Reconnect replay: attach with `since_entry: int | None`. When
  set, the context queries `session.entries()`, filters for
  `entry.id > since_entry`, emits one `session.replay.entry` per
  survivor, then a terminating `session.replay.done`. The
  per-connection lock is held for the full replay window so live
  fanout for the same connection buffers until `replay.done` is
  flushed.
- `SessionManager` subscribes to a caller-supplied kinds list and
  routes every event whose `session_id` has a live context to that
  context's `fanout`. Unknown sessions are dropped silently (the
  persister owns durability; a later attach with `since_entry`
  still catches up).
- CLI: `yaya session connections <id>` — deterministic empty
  snapshot in a transient CLI process, with a note pointing at
  `yaya serve` for live queries. Validates the `SessionManager`
  surface without needing a running kernel.
- Web adapter integration is deferred: the adapter plugin will
  consume `SessionManager` directly in a follow-up; this PR ships
  the kernel primitive + CLI validation only.

## Boundaries

### Allowed Changes
- src/yaya/kernel/session_context.py
- src/yaya/kernel/events.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/commands/session.py
- docs/dev/plugin-protocol.md
- docs/dev/web-ui.md
- specs/kernel-session-context.spec
- tests/kernel/test_session_context.py
- tests/bdd/features/kernel-session-context.feature
- tests/bdd/test_kernel_session_context.py

### Forbidden
- src/yaya/plugins/
- GOAL.md
- src/yaya/kernel/bus.py
- src/yaya/kernel/loop.py
- src/yaya/kernel/session.py
- src/yaya/kernel/session_persister.py

## Completion Criteria

Scenario: AC-01 — fanout reaches every attached connection
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_fanout_reaches_every_connection
  Level: unit
  Given a session context with two attached connections A and B
  When the manager fans out one event on the session
  Then both A and B receive the event exactly once

Scenario: AC-02 — event ordering is identical across connections
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_event_ordering_is_identical_across_connections
  Level: unit
  Given a session context with two attached connections
  When one hundred events are fanned out
  Then both connections observe the same sequence

Scenario: AC-03 — reconnect replay emits missed entries then a done sentinel
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_reconnect_replay_emits_missed_entries
  Level: unit
  Given a session tape with several entries
  When a connection attaches with since entry set to the id of an earlier entry
  Then the connection receives session replay entry events for every later entry
  And a single session replay done event closes the replay

Scenario: AC-04 — connection registry is bounded
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_connection_cap_is_enforced
  Level: unit
  Given a session context configured with max connections equal to two
  When a third connection attempts to attach
  Then a connection limit error is raised naming the cap

Scenario: AC-05 — heartbeat reap drops silent connections
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_heartbeat_reap_detaches_stale_connection
  Level: unit
  Given a session context with a short heartbeat timeout
  And a connection whose last seen is older than the timeout
  When the reap sweep runs
  Then the stale connection is detached with reason timeout
  And a session context detached event is emitted

Scenario: AC-06 — lifecycle events route on the kernel session
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_lifecycle_events_route_on_kernel_session
  Level: unit
  Given a bus subscriber listening to session context events on session id kernel
  When a connection attaches to a context for session S
  Then the subscriber observes the attached event
  And the envelope session id is kernel

Scenario: AC-07 — send failure detaches offender without blocking fanout
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_send_failure_detaches_and_preserves_fanout
  Level: unit
  Given a session context with one healthy connection and one connection whose send always raises
  When the manager fans out one event
  Then the healthy connection receives the event
  And the raising connection is detached with reason send failed

Scenario: AC-08 — close detaches every connection with shutdown reason
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_close_detaches_with_shutdown_reason
  Level: unit
  Given a session context with two attached connections
  When close is awaited
  Then both connections receive a session context detached event
  And the reason is shutdown

Scenario: AC-09 — manager routes bus events to the matching context
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_manager_routes_bus_events_to_matching_context
  Level: integration
  Given a session manager installed with a user message received subscription
  And a connection attached for session default
  When a user message received event is published on session default
  Then the connection receives the same event

Scenario: AC-10 — live event arriving during replay buffers behind the replay lock
  Test:
    Package: yaya
    Filter: tests/kernel/test_session_context.py::test_live_event_buffers_behind_replay
  Level: unit
  Given a session with several tape entries
  When a connection attaches with since entry zero
  And a live event is fanned out while the attach is still running
  Then the connection observes every replay entry before the live event

## Out of Scope

- Web adapter wiring of `SessionManager` — lands alongside the web
  adapter follow-up.
- Idle-eviction scheduler for empty contexts — event kind is in
  the catalog now; the background task lands with the web
  adapter.
- Cross-process or cross-host context sharing — forbidden by
  GOAL.md through 1.0.
- Inbound-queue serialization and turn ownership — the issue's
  `session.turn.*` events remain future work; this PR ships the
  fanout + replay primitive only.
