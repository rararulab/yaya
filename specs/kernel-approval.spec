spec: task
name: "kernel-approval"
tags: [kernel, approval, protocol, hitl]
---

## Intent

The yaya kernel ships a human-in-the-loop (HITL) approval runtime
that gates every tool call whose subclass declares
`requires_approval: ClassVar[bool] = True`. The runtime sits between
the kernel's tool dispatcher and the user-facing adapters: when a
gated tool is invoked, the runtime emits `approval.request`, waits
for the user's `approval.response`, and returns the decision to the
dispatcher. Approvals are session-aware — `approve_for_session`
caches the tuple `(tool_name, params_fingerprint)` so identical
future calls on the same session short-circuit without re-prompting.
Timeouts emit `approval.cancelled` and translate to
`tool.error(kind="rejected")`. All `approval.*` envelopes route on
the reserved `"kernel"` session to break the cross-session deadlock
documented in lesson #2.

## Decisions

- `src/yaya/kernel/approval.py` owns the runtime: `Approval`,
  `ApprovalResult`, `ApprovalRuntime`, `ApprovalCancelledError`,
  `ToolRejectedError`, module-level `install_approval_runtime` /
  `get_approval_runtime` / `uninstall_approval_runtime` helpers.
- Three new public event kinds added to the closed catalog in
  `kernel/events.py`: `approval.request`, `approval.response`,
  `approval.cancelled`. Corresponding `TypedDict` payload shapes
  mirror the prose in `docs/dev/plugin-protocol.md`.
- `Tool.pre_approve(ctx, *, session_id="kernel") -> bool` — additive
  kwarg with a default so existing Tool subclasses (test fixtures,
  bundled placeholders) keep compiling. The dispatcher passes the
  originating `ev.session_id` so the runtime's
  `approve_for_session` cache is keyed by the real tool-call
  session.
- `Tool.approval_brief(self) -> str` — overridable method, default
  renders `"<name>: <params>"` truncated to 80 chars.
- Lesson #2 routing: `ApprovalRuntime.request` publishes
  `approval.request` on session `"kernel"`; the runtime subscribes
  to `approval.response` on session `"kernel"`; adapters MUST publish
  their response on session `"kernel"` too. The originating
  tool-call session id is carried inside the `Approval` model
  (payload), NOT the envelope's routing session. Same-session
  routing would deadlock the drain worker that owns the dispatcher's
  `await pending_future`.
- Timeout default 60s, overridable per `ApprovalRuntime`. Timeout
  path: pop pending key (lesson #6 — no leak), emit
  `approval.cancelled`, raise `ApprovalCancelledError`. The
  dispatcher catches this and emits `tool.error(kind="rejected")`.
- `_session_allowlist` dict is in-memory only, never auto-evicted.
  Documented explicitly — session lifecycle belongs to the adapter
  and a proper cleanup hook lands alongside the session-scope work.
- `PluginRegistry.start` installs the runtime AFTER plugin load
  (adapters must be subscribed to `approval.request` before any
  prompt flows) but BEFORE `kernel.ready` (a tool call can land as
  soon as ready fires). `PluginRegistry.stop` uninstalls it BEFORE
  `kernel.shutdown` so pending futures see
  `ApprovalCancelledError(reason="shutdown")` instead of hanging on
  the per-request timeout.
- `KernelContext.bus` becomes a read-only property so the default
  `pre_approve` can resolve the runtime by bus identity without
  reaching into private state.
- Subscription source is `"kernel-approval"` — distinct from
  `"kernel"` so the bus recursion guard still lets handler failures
  surface through `plugin.error`.

## Boundaries

### Allowed Changes
- src/yaya/kernel/approval.py
- src/yaya/kernel/events.py
- src/yaya/kernel/tool.py
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- docs/dev/plugin-protocol.md
- specs/kernel-approval.spec
- tests/kernel/test_approval.py

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- GOAL.md

## Completion Criteria

Scenario: AC-01 — user approves, tool runs once
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_approve_runs_tool_once
  Level: integration
  Given a Tool subclass with requires approval true
  And an adapter that answers approval request with approve
  When a tool call request is emitted for the tool
  Then the tool run method is invoked exactly once
  And a tool call result event is emitted with ok true

Scenario: AC-02 — user rejects, tool is blocked
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_reject_blocks_tool
  Level: integration
  Given a Tool subclass with requires approval true
  And an adapter that answers approval request with reject and feedback no thanks
  When a tool call request is emitted for the tool
  Then the tool run method is not invoked
  And a tool error event is emitted with kind rejected whose brief mentions no thanks

Scenario: AC-03 — approve for session short-circuits future prompts
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_approve_for_session_short_circuits
  Level: integration
  Given an adapter that answers the first approval with approve for session
  When two identical tool call requests are published on the same session
  Then exactly one approval request event is observed by the adapter
  And both tool call result events carry ok true

Scenario: AC-04 — 60s timeout cancels the approval
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_timeout_emits_cancelled_and_rejects_tool
  Level: unit
  Given an approval runtime with a very short timeout
  And no adapter subscribed to approval request
  When the runtime request method is awaited
  Then an approval cancelled event is emitted with reason timeout
  And an approval cancelled error is raised

Scenario: AC-05 — approval events route on the kernel session to avoid deadlock
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_approval_events_route_on_kernel_session
  Level: unit
  Given an adapter subscribed to approval request
  When the runtime issues a request from inside a tool call session worker
  Then the approval request envelope carries session id kernel
  And the resolve path does not deadlock the originating session worker

Scenario: AC-06 — shutdown cancels pending approvals
  Test:
    Package: yaya
    Filter: tests/kernel/test_approval.py::test_stop_cancels_pending
  Level: unit
  Given a pending approval request
  When the approval runtime stop method is called
  Then the awaiting caller observes an approval cancelled error with reason shutdown

## Out of Scope

- Persistent allowlists (always allow ls) — in-memory only for 0.2.
- Session lifecycle cleanup for the approve-for-session cache —
  tracked alongside the session-scope work.
- Migrating the bundled `tool_bash` plugin to opt in to
  `requires_approval=True` — a follow-up PR (the legacy plugin stays
  on the `on_event` path for now).
- Policy-file gating (YAML-driven "always allow X with args Y").
