spec: task
name: "kernel-compaction"
tags: [kernel, compaction, context]
---

## Intent

Long sessions accumulate messages that exceed the LLM provider's
context window. The kernel owns a small, tokenizer-agnostic
compaction layer sitting on top of the tape store (#32): a pluggable
`Summarizer` protocol, a char-count heuristic estimator, a manual
`Session.compact()` helper, and an optional auto-trigger manager
driven by a configurable token threshold. Compaction appends a
`kind="compaction"` anchor carrying the summary; the default tape
context injects that summary as a `role="system"` message so the LLM
sees a compressed history instead of the full log.

## Decisions

- `src/yaya/kernel/compaction.py` owns the runtime:
  `Summarizer` Protocol, `estimate_text_tokens`, `should_auto_compact`,
  `compact_session`, `CompactionManager`, `install_compaction_manager`.
- `Session.compact(summarizer, ...)` is a thin method wrapper around
  `compact_session` so callers holding a `Session` never need to
  import `compaction` directly. Lazy import inside the method avoids
  a module-level cycle between `session` and `compaction`.
- Three new public event kinds land in `events.py`:
  `session.compaction.started`, `session.compaction.completed`,
  `session.compaction.failed`. TypedDict payloads live alongside the
  existing session.* shapes.
- All three compaction events route on `session_id="kernel"` per the
  lesson-2 routing rule. Publishing on the originating session would
  deadlock its FIFO because the handler that triggered the check is
  still draining it. Adapters correlate events to the target tape via
  the `target_session_id` payload field.
- Token-count heuristic is char-based
  (`len(text) // 4`). Tokenizer-agnostic so the kernel never depends
  on a vendor tokenizer. Stable for fixed input (AC-03 relies on
  this).
- Default context (`select_messages`) detects compaction anchors via
  `payload.state.kind == "compaction"` and injects a `role="system"`
  message carrying the stored summary. Non-compaction anchors stay
  invisible — they were never meant to land on the LLM.
- `CompactionManager` is a bus subscriber. Single in-flight per
  session id, three-attempt retry with exponential backoff, bounded
  in-flight / attempts maps (lesson #6: unbounded dicts leak), FIFO
  eviction when the cap is hit. Disabled sessions skip silently
  after the retry cap — the failure event was already emitted.
- `KernelConfig.compaction` (`CompactionConfig`): `auto=False`
  (opt-in), `threshold_tokens=50_000`, `target_tokens=10_000`. A
  `threshold_tokens=0` disables the check even when `auto=True`.
- CLI: `yaya session compact <id>` runs the heuristic fallback
  summariser so operators have a useful manual path without a running
  kernel. `yaya session show --since-compact` returns only entries
  after the most recent anchor.
- `SessionPersister._SKIP_KINDS` is extended to include the three
  compaction kinds so even if a future wiring mistake publishes them
  on a non-kernel session they never land on a user tape.

## Boundaries

### Allowed Changes
- src/yaya/kernel/compaction.py
- src/yaya/kernel/session.py
- src/yaya/kernel/tape_context.py
- src/yaya/kernel/events.py
- src/yaya/kernel/config.py
- src/yaya/kernel/session_persister.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/commands/session.py
- docs/dev/plugin-protocol.md
- specs/kernel-compaction.spec
- tests/kernel/test_compaction.py
- tests/kernel/test_events.py
- tests/cli/test_session.py
- tests/bdd/features/kernel-compaction.feature
- tests/bdd/test_kernel_compaction.py

### Forbidden
- src/yaya/plugins/
- GOAL.md
- src/yaya/kernel/approval.py
- src/yaya/kernel/bus.py
- src/yaya/kernel/tool.py
- src/yaya/kernel/loop.py

## Completion Criteria

Scenario: AC-01 — manual compact appends a compaction anchor
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_manual_compact_appends_anchor
  Level: unit
  Given a session with five user messages since the last anchor
  When session compact runs with a fake summariser
  Then the tape has a new anchor with state kind equal to compaction
  And the anchor state carries the summary string

Scenario: AC-02 — empty post-anchor window is a no-op
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_compact_empty_postanchor_is_noop
  Level: unit
  Given a fresh session with only the bootstrap anchor
  When session compact runs with a fake summariser
  Then the summary string is empty
  And the tape still has only the bootstrap anchor

Scenario: AC-03 — estimator is deterministic
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_estimator_is_deterministic
  Level: unit
  Given a fixed list of tape entries
  When estimate text tokens runs twice over the same entries
  Then both calls return the same positive integer

Scenario: AC-04 — default context injects the summary as system
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_post_compaction_context_injects_summary
  Level: unit
  Given a session with two pre compaction messages then a compaction anchor
  When default context is rendered from the tape
  Then the returned messages start with a role system summary message

Scenario: AC-05 — summariser failure is translated to a failed event
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_compact_failure_emits_failed_and_does_not_anchor
  Level: unit
  Given a session with one pre compaction message
  When session compact runs with an exploding summariser
  Then a session compaction failed event is emitted
  And no compaction anchor is appended

Scenario: AC-06 — auto manager triggers once past the threshold
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_manager_auto_triggers_once_past_threshold
  Level: integration
  Given a running compaction manager with a low threshold
  When a user message received event pushes the tape past threshold
  Then the summariser is invoked at least once

Scenario: AC-07 — single in flight guard
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_manager_single_inflight_guard
  Level: integration
  Given a compaction manager with a slow summariser
  When three user message received events arrive back to back
  Then the summariser is invoked exactly once while the first call is pending

Scenario: AC-08 — fork compaction does not mutate the parent tape
  Test:
    Package: yaya
    Filter: tests/kernel/test_compaction.py::test_fork_compact_does_not_mutate_parent
  Level: unit
  Given a parent session with three user messages
  When the parent forks a child and the child compacts
  Then the parent tape entry count is unchanged

Scenario: AC-09 — yaya session compact cli happy path
  Test:
    Package: yaya
    Filter: tests/cli/test_session.py::test_session_compact_happy_path
  Level: integration
  Given a seeded session with a couple of messages
  When yaya json session compact default runs
  Then the exit code is zero
  And the json output has action equal to session dot compact
