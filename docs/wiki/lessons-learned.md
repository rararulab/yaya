# Agent Lessons Learned

Rolling log of recurring review findings, anti-patterns, and hazards
that bit us during implementation. **Read this before starting any
non-trivial kernel/plugin work.** Every PR whose review surfaces a new
hazard appends one entry here in the same PR.

Entry shape:

```
## N. <short name>
**Symptom**  — what the review or a probe flagged.
**Root cause** — why it happens.
**Rule** — what to do instead.
**Reference** — PR / file / line.
```

Keep entries short and actionable. If a rule generalises, lift it into
`docs/dev/plugin-protocol.md` or the relevant `AGENT.md`.

---

## 1. asyncio.Lock is not reentrant — never serialize per-key FIFO with a shared lock

**Symptom** — Handlers that re-publish on their own session deadlocked
for 30 s before the outer `wait_for` cancelled them; the cancellation
was surfaced as a spurious empty-message `plugin.error` ("plugin 'tool'
raised while handling 'tool.call.request': ").

**Root cause** — `asyncio.Lock` held by the worker task is re-acquired
by the same task; asyncio locks deadlock on same-task re-acquisition.
FIFO per session was implemented as `async with session_locks[sid]:`
around fan-out, which is the wrong primitive for a serial delivery
contract.

**Rule** — Use a **per-key worker task + `asyncio.Queue`** pattern for
serial delivery. Publishers enqueue; the worker drains. FIFO is
preserved because one worker owns one queue. Re-entry is impossible by
construction.

**Reference** — PR #21, `src/yaya/kernel/bus.py`.

---

## 2. Heuristic "external-vs-reentrant" must detect **any** worker context, not just the target's

**Symptom** — Cross-session cycle deadlocked: handler on session `s1`
published to `s2` and awaited; `s2` handler published to `s1` and
awaited; both workers blocked on each other's completion future.

**Root cause** — The re-entry check used
`current_task() is session_workers[event.session_id]`. That only
detects **same-session** re-entry. A task running inside `s1`'s worker
that publishes to `s2` is NOT `s2`'s worker, so the check missed it and
the caller awaited delivery, which cannot complete while `s1` is
blocked.

**Rule** — Detect "am I inside **any** worker" via a
`contextvars.ContextVar[bool]` set by the worker for the duration of
its loop body. ContextVars propagate across `await` within the same
task but are copied (and therefore resettable) when you spawn a new
task. This gives the right granularity.

**Reference** — PR #21 round 2, `src/yaya/kernel/bus.py` `_IN_WORKER`.

---

## 3. `except BaseException` swallows `asyncio.CancelledError`

**Symptom** — Deliberately cancelling a `publish` task produced a
spurious `plugin.error` ("plugin 'slow' raised while handling
'kernel.ready'") because the handler's `CancelledError` was caught,
reported, and a synthetic error emitted instead of propagating.

**Root cause** — In modern asyncio, `CancelledError` **must** propagate
so `gather`, `wait_for`, and `Task.cancel` can unwind cleanly.

**Rule** — Either narrow to `except Exception` (CancelledError is a
BaseException subclass, not an Exception subclass, in Python 3.8+), or
explicitly `except asyncio.CancelledError: raise` before the general
handler. Never swallow cancellation.

**Reference** — PR #21, `src/yaya/kernel/bus.py` `_deliver`.

---

## 4. TypedDict `total=False` is the wrong default for payloads whose fields split required/optional

**Symptom** — `mypy --strict` accepted payloads missing required keys
(`text`, `content`, `usage`, etc.) because the TypedDict marked them
optional via `total=False`. The public-contract tables in
`plugin-protocol.md` show required vs optional (`field` vs `field?`).

**Root cause** — Subagent heuristic: "if any field is optional, just
make the whole dict `total=False`." That weakens the type contract and
silently lets the protocol drift.

**Rule** — Default `TypedDict` to `total=True` (the default). For each
optional field use `typing.NotRequired[T]`. Write one table-driven test
that compares `__required_keys__` / `__optional_keys__` against the
protocol spec so drift fails CI.

**Reference** — PR #21, `src/yaya/kernel/events.py`,
`tests/kernel/test_events.py::test_typed_dict_required_optional_partition`.

---

## 5. `from __future__ import annotations` breaks `NotRequired` partitioning

**Symptom** — After switching payloads to `NotRequired[T]`, the
introspection test still saw every field as required.

**Root cause** — PEP 563 string annotations defer evaluation. With
`from __future__ import annotations`, `TypedDict` bodies are stored as
strings and `__required_keys__` / `__optional_keys__` fall back to
treating all fields as required at class-construction time.

**Rule** — In modules that define `TypedDict` classes using
`NotRequired`, do **not** enable `from __future__ import annotations`.
Document the choice in the module docstring so future edits don't
reintroduce it.

**Reference** — PR #21, `src/yaya/kernel/events.py` module docstring.

---

## 6. Unbounded per-key dicts leak in long-running async processes

**Symptom** — `_session_locks` dict grew to 1000 entries after 1000
one-shot session ids (one WS connection each). Each entry never
released.

**Root cause** — Static "register on first use, never remove" dicts
keyed by user-supplied ids leak by construction.

**Rule** — Prefer self-cleaning state. In the queue-worker pattern,
the worker's `finally` block removes its own queue + task entry when
the queue drains. Verify with a burst test:
`bus._session_queues` must return to zero after N one-shot publishes.

**Reference** — PR #21, `src/yaya/kernel/bus.py` `_drain` `finally`,
`tests/kernel/test_bus.py::test_session_queue_releases_when_idle`.

---

## 7. Default dataclass `__eq__` breaks identity-based `list.remove`

**Symptom** — Subscribing the same handler+source twice, then
unsubscribing one, could remove either entry because
`_Subscriber` had structural equality over `(kind, handler, source)`.

**Root cause** — `@dataclass(slots=True)` generates `__eq__` by
default. `list.remove(x)` uses `==`, so identity is lost.

**Rule** — When the class is an **identity-keyed handle** (subscription
tokens, resource IDs, pending-request futures), set
`@dataclass(eq=False)` so equality falls back to `is`.

**Reference** — PR #21, `src/yaya/kernel/bus.py` `_Subscriber`.

---

## 8. Subagent "findings for the next issue" are usually bugs in THIS PR

**Symptom** — PR #21's self-report listed "the agent loop must not
await round-trip events from inside a handler on the same session or it
will deadlock" as a note for the next issue. That was not a next-issue
concern; it was a design flaw IN the current bus.

**Root cause** — Subagents sometimes defer problems they noticed with
"document it for the next PR" instead of fixing them. This transfers
debt and forces downstream consumers to work around infrastructure.

**Rule** — If a subagent flags a hazard or a design trade-off in the
code it just wrote, the reviewer must treat it as a finding in the
current PR and decide whether to fix now or accept. Do **not**
auto-defer.

**Reference** — PR #21 round 2 (retro-fix of round-1 hazards).

---

## 9. No speculative state "for future extensions"

**Symptom** — PR #21 round 1 shipped an `_in_flight: set[Task]` that
was never written to and an empty `if TYPE_CHECKING: pass` block "for
future fire-and-forget extensions." Dead code.

**Root cause** — "I might need this later" accumulates abstractions
without tests to justify them.

**Rule** — Project rule: no speculative abstractions. Delete dead
state. Re-add it with tests when the feature actually lands.

**Reference** — `AGENT.md` §4 Constraints + PR #21.

---

## 10. Silent no-op primitives are a UX bug

**Symptom** — `publish()` on a closed bus silently returned without
delivering. Callers racing with shutdown had no signal the event was
dropped.

**Root cause** — Defensive-return without observability trades a
visible crash for an invisible failure.

**Rule** — Silent drops log a WARNING naming what was dropped (event
kind, task name, etc.). For dispatch primitives on shut-down resources,
a warning is the minimum; consider raising if callers are expected to
handle the state.

**Reference** — PR #21, `bus.py::publish` `Note:` docstring +
`test_publish_after_close_logs_warning`.

---

## 11. Empirical probes beat static reasoning

**Symptom** — "Same-session re-entry hangs because asyncio.Lock isn't
reentrant" looked subtle on paper; a 20-line probe reproduced it in
one run. Same for cross-session cycle and plugin.error cascade.

**Root cause** — Async deadlocks do not produce a stack trace on the
blocking call; they hide until a timeout fires elsewhere.

**Rule** — When reviewing async infrastructure, write a minimal probe
(`asyncio.run` a harness; `wait_for` with a short timeout) to reproduce
each hypothesised hazard. Ship the successful probe as a regression
test.

**Reference** — PR #21 review transcript.

---

## 12. Code-review rounds re-enter until genuinely clean

**Symptom** — Round-1 fixes introduced TWO new cross-session deadlock
hazards; they were only caught by running `code-review-expert` a second
time on the updated PR.

**Root cause** — A fixup can subtly reshape the design. Approving after
one round on load-bearing infra is risky.

**Rule** — After any fixup on infra PRs (kernel, event bus, plugin
ABI), re-run the full review skill. Merge only after a round produces
zero new findings at P0/P1.

**Reference** — PR #21 two rounds.

---

## 13. Document non-obvious design decisions in the spec's `## Decisions`

**Symptom** — Queue-vs-lock, correlation-via-event-id, ContextVar
reset for turn tasks — each is a non-obvious design choice that
affected subsequent issues.

**Root cause** — Implementation-level decisions buried in code
comments are invisible to the next agent reading only the spec.

**Rule** — `specs/<slug>.spec.md`'s `## Decisions` section captures
every **non-obvious** design choice that affects the external contract
or downstream issues. One bullet per decision, with the **why**.

**Reference** — `docs/dev/agent-spec.md` + `docs/dev/architecture.md`.

---

## 14. Pre-commit hook drift: `just check-all` is currently broken

**Symptom** — Pre-commit runs a pinned `ruff` that doesn't know
`target-version = "py314"`. `just check-all` fails on a config the
direct `uv run ruff check` accepts.

**Root cause** — `.pre-commit-config.yaml` pins a ruff version older
than Python 3.14 support.

**Rule** — Source of truth for quality gates is the **direct tool
invocations** (`uv run ruff check`, `uv run mypy`, `uv run pytest`).
CI uses those. Treat `just check-all` as advisory until the pre-commit
pin is bumped. Open a follow-up chore issue to fix it; do NOT skip
hooks with `--no-verify`.

**Reference** — PR #21 subagent reports; `.pre-commit-config.yaml`.

---

## 15. Silent "no request_id" drops hang plugin authors

**Symptom** — A plugin forgets to echo ``request_id`` on its response
event. The agent loop hangs for ``step_timeout_s`` (60 s default), then
surfaces ``kernel.error: step_timeout`` with no indication the real
cause was a missing correlation field.

**Root cause** — ``_RequestTracker.resolve`` silently returns when the
incoming event has no ``request_id``, treating it as "maybe a rogue
response from outside the loop". That silent path is indistinguishable
from "correlation id doesn't match any in-flight request", so the loop
waits for a response that will never match.

**Rule** — When a protocol-defined correlation field is missing, log a
WARNING that names the offending event kind and source. "Late arrival
for a cancelled turn" is debug-level; "missing required correlation
field" is warning-level. The 60-second wait is not the error — the
silence is.

**Reference** — PR #38, ``src/yaya/kernel/loop.py`` ``_RequestTracker.resolve``.

---

## 16. Reviewers: verify the flagged pattern is actually wrong

**Symptom** — PR #38 round-2 review flagged `except TypeError, ValueError:`
(without parentheses) as an "unidiomatic hazard" and asked the author
to parenthesize it. The request was bogus: PEP 758 (accepted in
Python 3.12) makes unparenthesized multi-type `except` clauses valid
and semantically identical to the parenthesized form. Worse, ruff
with `target-version = "py314"` actively **normalizes** the
parenthesized form back to unparenthesized, so committing the
"fix" would have failed `ruff format --check` in CI.

**Root cause** — Reviewer applied pre-3.12 Python style intuition
without re-checking the language spec + the ruff formatter config
in `pyproject.toml`. Pattern-level lints are especially vulnerable
to this — "looks wrong to me" often means "unfamiliar to me".

**Rule** — Before filing a pattern-level finding: (a) confirm the
current Python version (`pyproject.toml` `requires-python`), (b)
confirm the configured ruff/mypy target-version would flag it on its
own, (c) test the diff in a scratch file with `ruff format` and
`ruff check` to see what CI actually enforces. If the formatter
disagrees with the finding, the finding is wrong — trust the
formatter, not intuition.

**Reference** — PR #38 review round 2 (aborted). Cf. lesson #11
(empirical probes beat static reasoning) — it applies to reviewers
too, not just authors.

---

## How to use this doc

- Before starting a PR that touches the kernel, event bus, plugin ABI,
  agent loop, or any async infrastructure: **read every entry**.
- When dispatching a subagent: reference this doc in the prompt's
  "Read first" list.
- When a review turn surfaces a hazard that is **not** listed here:
  append an entry in the same PR. The review cannot pass until the
  hazard is captured.
- When a rule generalises beyond a single PR, lift it into
  `docs/dev/plugin-protocol.md` or the relevant `AGENT.md`. This file
  is for patterns, not a permanent spec.
