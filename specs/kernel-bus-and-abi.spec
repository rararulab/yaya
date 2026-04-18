spec: task
name: "kernel-bus-and-abi"
tags: [kernel, bus, abi]
---

## Intent

yaya's kernel is the product's bottom layer: an event bus, a plugin ABI,
and a closed public event catalog. Every other capability — adapters,
LLM providers, tools, strategies, memory, skills — depends on this
layer. This contract pins down the observable behavior of the bus and
the shape of the event envelope before any plugin or the agent loop
can be built on top.

## Decisions

- Closed public event catalog lives in `src/yaya/kernel/events.py`; the
  `new_event` factory rejects unknown public kinds with `ValueError`,
  mirroring the tables in `docs/dev/plugin-protocol.md`.
- `new_event` returns an `Event` envelope carrying `id`, `kind`,
  `session_id`, `ts`, `source`, `payload` fields — the shape every
  subscriber observes.
- The `x.<plugin>.<kind>` extension namespace routes through the
  event bus without payload type checks, letting plugins ship private
  events alongside the public catalog.
- `EventBus` is asyncio-native and delivers each published event to
  every subscriber registered for that kind; a raising subscriber is
  isolated so healthy subscribers still receive the event and a
  synthetic `plugin.error` is emitted.
- Per-subscriber 30 s timeout (`DEFAULT_HANDLER_TIMEOUT_S`) with FIFO
  per-session ordering via a single drain worker so handlers may
  publish on the same session without deadlocking the bus.

## Boundaries

### Allowed Changes
- src/yaya/kernel/bus.py
- src/yaya/kernel/events.py
- src/yaya/kernel/plugin.py
- tests/kernel/test_bus.py
- tests/kernel/test_events.py
- tests/kernel/test_plugin.py
- specs/kernel-bus-and-abi.spec

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Bus delivers event to subscribers registered for the kind
  Test:
    Package: yaya
    Filter: tests/kernel/test_bus.py::test_delivers_to_subscriber
  Level: unit
  Given a running EventBus
  And a subscriber registered for "user.message.received"
  When a "user.message.received" event is published
  Then the subscriber receives the event with envelope fields populated

Scenario: Error path — raising subscriber is isolated and emits plugin.error
  Test:
    Package: yaya
    Filter: tests/kernel/test_bus.py::test_raising_subscriber_isolated
  Level: unit
  Given a running EventBus
  And a subscriber that raises on receipt
  And a second healthy subscriber for the same kind
  When the event is published
  Then the healthy subscriber still receives the event
  And a synthetic "plugin.error" event is emitted by the bus

Scenario: Event envelope carries id kind session_id ts source payload fields
  Test:
    Package: yaya
    Filter: tests/kernel/test_events.py::test_envelope_fields
  Level: unit
  Given the events module
  When new_event is called with a known public kind
  Then the returned Event envelope has id, ts, source, session_id, kind, and payload fields

Scenario: Extension namespace routes arbitrary payload through the bus
  Test:
    Package: yaya
    Filter: tests/kernel/test_bus.py::test_extension_namespace_routes
  Level: unit
  Given a running EventBus
  And a subscriber for "x.foo.bar" in the extension namespace
  When an "x.foo.bar" event is published with an arbitrary payload
  Then the subscriber receives it unchanged without type checks

Scenario: Error path — closed public catalog rejects unknown kinds
  Test:
    Package: yaya
    Filter: tests/kernel/test_events.py::test_rejects_unknown_public_kind
  Level: unit
  Given the events module with its closed public catalog
  When new_event is called with unknown public kind "nonsense.unknown"
  Then ValueError is raised referencing the closed public catalog

Scenario: FIFO per-session handler publishes on the same session without deadlock
  Test:
    Package: yaya
    Filter: tests/kernel/test_bus.py::test_handler_can_emit_on_same_session
  Level: unit
  Given a running EventBus with a single drain worker per session
  And a subscriber that publishes a follow-up event on the same session
  When the first event is delivered under the 30 s per-subscriber timeout
  Then the follow-up event is enqueued and delivered in FIFO order without deadlocking

## Out of Scope

- Wildcard subscription routing (exact-kind only at 1.0).
- Governance amendments to the public event catalog (tracked separately).
