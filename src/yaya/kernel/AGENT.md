# src/yaya/kernel — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
The kernel. Event bus, plugin ABI, closed public event catalog, and (later) the plugin registry and fixed agent loop. Every other yaya capability depends on this layer. See [GOAL.md](../../../GOAL.md) and [docs/dev/plugin-protocol.md](../../../docs/dev/plugin-protocol.md).

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../docs/dev/plugin-protocol.md) is the authoritative contract — this module is its Python mirror; drift fails review.
- `tests/kernel/` is ground truth for behavior; ≥80% coverage enforced by `pyproject.toml`.
- `mypy --strict` + ruff are the import-graph enforcers: the kernel MUST NOT import from `cli`, `plugins`, or `core`.
- BDD contract: [`specs/kernel-bus-and-abi.spec.md`](../../../specs/kernel-bus-and-abi.spec.md); `agent-spec guard` rejects unbound scenarios.

## Constraints
- `events.py` — closed `PublicEventKind` Literal, per-kind `TypedDict` payloads, `Event` dataclass, `new_event()` factory. New public kind = governance amendment (protocol doc + GOAL.md + this module in the same PR).
- `plugin.py` — `Category` StrEnum, runtime-checkable `Plugin` Protocol, `KernelContext` (emit stamps `source` with the plugin name; plugins cannot forge identity).
- `bus.py` — asyncio pub/sub. Exact-kind routing (no wildcards at 1.0). Per-subscriber 30s timeout. FIFO per `session_id` via a single drain worker task over a per-session `asyncio.Queue`; handlers may call `publish` / `ctx.emit` on the same session while running, the follow-up event is enqueued and delivered after the current handler returns (no re-entry hazard). Failing handlers produce a synthetic `plugin.error` enqueued on the `"kernel"` session (`source = "kernel"`); kernel-origin failures do NOT re-emit (recursion guard).
- `loop.py` — fixed agent loop (the scheduler). `AgentLoop(bus, config)` subscribes to `user.message.received`, `user.interrupt`, and the four response kinds (`strategy.decide.response`, `llm.call.response`, `llm.call.error`, `memory.result`, `tool.call.result`); emits requests in the order frozen by [`docs/dev/plugin-protocol.md`](../../../docs/dev/plugin-protocol.md) (strategy → optional memory → llm → zero-or-more tools → `assistant.message.done` → optional `memory.write`). Strategy plugins choose the `next` step (`"llm" | "tool" | "memory" | "done"`); the loop owns the order. **Correlation** is by originating event `id`: each outbound request's `id` is mirrored back as `payload.request_id` on the corresponding response event, and a private `_RequestTracker` resolves awaiters by that key. Per-turn tasks are spawned with an empty `contextvars.Context()` so the bus's `_IN_WORKER` ContextVar is reset for the turn — without this the loop's publishes inside a turn would fire-and-forget and correlation futures would never resolve. Guards: `max_iterations` trips `kernel.error` (`source="agent_loop"`, `message="max_iterations_exceeded"`); `user.interrupt` cancels the turn for that session. Depends only on `bus`, `events`, `plugin` — no `cli`, `plugins`, or `core` imports, and no concrete plugin imports.
- **`source="kernel"` is reserved for kernel-internal code.** The plugin registry (issue #13) will enforce that plugin subscriptions cannot claim this source. Until the registry lands, this is an honor-system invariant — the recursion guard in the bus trusts it to short-circuit loops.
- Stdlib only (plus `loguru` only if a plugin-facing logger demands it). No module-level side effects at import.

## Interaction (patterns)
- New public event kind ⇒ update `PublicEventKind`, the per-kind `TypedDict`, [`docs/dev/plugin-protocol.md`](../../../docs/dev/plugin-protocol.md), and `GOAL.md`'s category table in the SAME PR. Label `governance`.
- Do NOT add wildcard subscription at 1.0 — kind match is exact. Adapters that want many kinds subscribe many times.
- Do NOT let plugins emit `plugin.error` or `kernel.error` directly. Only the kernel synthesizes them.
- Do NOT import from `cli`, `plugins`, or `core`. Direction is one-way: everything depends on `kernel`, never the reverse.
- Do NOT add a "fast path" for bundled plugins. They use the same ABI.

## Budget & Loading
- Authoritative contract: [../../../docs/dev/plugin-protocol.md](../../../docs/dev/plugin-protocol.md).
- Layering: [../../../docs/dev/architecture.md](../../../docs/dev/architecture.md).
- BDD contract shape: [../../../docs/dev/agent-spec.md](../../../docs/dev/agent-spec.md).
- Sibling indexes: [../AGENT.md](../AGENT.md) · [../../../tests/AGENT.md](../../../tests/AGENT.md).
