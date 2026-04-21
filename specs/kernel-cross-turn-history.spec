spec: task
name: "kernel-cross-turn-history"
tags: [kernel, agent-loop, sessions]
---

## Intent

The kernel agent loop must carry conversation history across turns
inside one session so the LLM sees prior user + assistant exchanges
on every follow-up message. The canonical history already lives on
the session tape (the persister mirrors every public event onto the
tape per #153); the loop simply reads it back at turn start and
feeds it into the outgoing strategy / LLM messages list. Compaction
anchors partition the tape: only the post-anchor tail plus the
summary carry forward, matching the contract in
`yaya.kernel.tape_context.select_messages`.

## Decisions

- `AgentLoop.__init__` accepts an optional `session_store` and
  `workspace` pair. When `None`, the loop preserves the 0.1
  single-message fallback so `yaya hello` and unit tests keep
  working without a store.
- `_run_turn` opens the session via `store.open(workspace,
  session_id)` and projects every `kind="message"` tape entry to
  `{role, content}`. The incoming `user.message.received` text is
  always the trailing message, deduplicated against the persister
  race that may have already landed the current message on the tape.
- Compaction anchors (`kind="anchor"` with
  `payload.state.kind == "compaction"`) elide every message before
  them and inject the anchor's `summary` as a leading
  `role="system"` message. Only the most recent compaction anchor
  applies — pre-anchor content never resurfaces.
- Hydration failures (store raises on `open`, tape read error) fall
  back to the single-message state and log at EXCEPTION level; a
  broken tape must not take the session worker down.
- The public event catalog and `_TurnState` shape are unchanged —
  this is kernel-internal plumbing only.

## Boundaries

### Allowed Changes
- src/yaya/kernel/loop.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/commands/serve.py
- tests/kernel/test_loop.py
- tests/bdd/features/kernel-cross-turn-history.feature
- tests/bdd/test_kernel_cross_turn_history.py
- specs/kernel-cross-turn-history.spec
- docs/dev/plugin-protocol.md
- docs/dev/architecture.md

### Forbidden
- src/yaya/kernel/events.py
- src/yaya/kernel/bus.py
- src/yaya/kernel/session.py
- src/yaya/kernel/session_persister.py
- src/yaya/kernel/compaction.py
- src/yaya/plugins/
- src/yaya/core/
- GOAL.md

## Completion Criteria

Scenario: Prior user and assistant messages are loaded from the tape into the next turn
  Test:
    Package: yaya
    Filter: tests/bdd/test_kernel_cross_turn_history.py::test_prior_messages_hydrate_next_turn
  Level: unit
  Given a session tape with one completed user/assistant exchange
  And an AgentLoop wired to the session store for that workspace
  When a second user.message.received event is published on the same session
  Then the strategy.decide.request carries the prior user message, the prior assistant reply, and the new user message in order

Scenario: The most recent compaction anchor elides pre-anchor history
  Test:
    Package: yaya
    Filter: tests/bdd/test_kernel_cross_turn_history.py::test_compaction_anchor_elides_prefix
  Level: unit
  Given a session tape with two pre-compaction messages followed by a compaction anchor and one post-anchor message
  And an AgentLoop wired to the session store for that workspace
  When a new user.message.received event arrives on the same session
  Then the strategy.decide.request omits the pre-anchor messages and starts with the compaction summary as a system message

Scenario: Loops without a session store preserve the 0.1 single-message fallback
  Test:
    Package: yaya
    Filter: tests/bdd/test_kernel_cross_turn_history.py::test_no_store_fallback
  Level: unit
  Given an AgentLoop constructed without a session store
  When a user.message.received event arrives
  Then the strategy.decide.request carries only the incoming user message

## Out of Scope

- Click-to-resume in the web UI (follow-up issue).
- New public event kinds or tape entry shapes.
- Rewriting the persister or the compaction anchor shape.
