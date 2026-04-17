# kernel-bus-and-abi

## Intent

yaya's kernel is the product's bottom layer: an event bus, a plugin ABI, and a
closed public event catalog. Every other capability — adapters, LLM providers,
tools, strategies, memory, skills — depends on this layer. This contract
pins down the observable behavior of the bus and the shape of the event
envelope before any plugin or the agent loop can be built on top.

## Decisions

- Event catalog lives in `src/yaya/kernel/events.py` as a `typing.Literal`
  (`PublicEventKind`) plus per-kind `TypedDict` payload types, mirroring
  the tables in `docs/dev/plugin-protocol.md`. New public kinds require a
  governance amendment that touches this module, the protocol doc, and
  `GOAL.md` together.
- `Event` is a frozen-ish `dataclass` with fields `id`, `kind`, `session_id`,
  `ts`, `source`, `payload`. `new_event()` is the only sanctioned factory
  and rejects unknown public kinds with `ValueError`; the `x.<plugin>.<kind>`
  extension namespace is accepted without type checks.
- `Plugin` is a `@runtime_checkable` `Protocol`. `Category` is a `StrEnum`
  with exactly the six categories from `plugin-protocol.md`.
- `EventBus` is asyncio-native. Exact-kind routing at 1.0 (no wildcards).
  Per-subscriber 30s timeout (`DEFAULT_HANDLER_TIMEOUT_S`). FIFO per
  `session_id` via per-session `asyncio.Lock`. A raising or hanging
  handler is isolated; the bus emits a synthetic `plugin.error`
  (`source = "kernel"`). Kernel-origin failures do not re-emit.
- Stdlib only. No imports from `cli`, `plugins`, or `core`.

## Boundaries

- **Allowed**:
  - `src/yaya/kernel/**`
  - `tests/kernel/**`
  - `specs/kernel-bus-and-abi.spec.md`
- **Forbidden**: everywhere else. This PR must not touch `src/yaya/cli/`,
  `src/yaya/core/`, `src/yaya/plugins/`, `pyproject.toml` dependencies,
  `docs/dev/plugin-protocol.md`, or `GOAL.md`.

## Completion Criteria (BDD)

Scenario: Bus delivers an event to subscribers
  Given a running EventBus
  And a subscriber registered for "user.message.received"
  When a "user.message.received" event is published
  Then the subscriber receives the event with envelope fields populated
  Test: tests/kernel/test_bus.py::test_delivers_to_subscriber

Scenario: A raising subscriber does not crash the bus
  Given a running EventBus
  And a subscriber that raises on receipt
  And a second healthy subscriber for the same kind
  When the event is published
  Then the healthy subscriber still receives the event
  And a "plugin.error" event is emitted
  Test: tests/kernel/test_bus.py::test_raising_subscriber_isolated

Scenario: Events carry the mandated envelope
  Given the events module
  When new_event is called with a known public kind
  Then the returned Event has id, ts, source, session_id, kind, and payload fields
  Test: tests/kernel/test_events.py::test_envelope_fields

Scenario: Extension events route without type checks
  Given a running EventBus
  And a subscriber for "x.foo.bar"
  When an "x.foo.bar" event is published with an arbitrary payload
  Then the subscriber receives it unchanged
  Test: tests/kernel/test_bus.py::test_extension_namespace_routes

Scenario: Closed event catalog rejects unknown public kinds
  Given the events module
  When code calls new_event with kind "nonsense.unknown"
  Then ValueError is raised referencing the closed catalog
  Test: tests/kernel/test_events.py::test_rejects_unknown_public_kind
