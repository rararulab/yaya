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

**Rule** — `specs/<slug>.spec`'s `## Decisions` section captures
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

## 17. "Consecutive" vs "cumulative" in failure policies

**Symptom** — PR #49 documented the registry's failure threshold as
"consecutive plugin.error events" in both the spec and protocol doc,
then implemented it as a monotonic counter: 2 errors + 1 success + 1
error = permanent unload.

**Root cause** — Incrementing a counter on error without ever resetting
it is the obvious implementation; "consecutive" requires a reset hook
on success that is easy to forget because the success path doesn't
naturally touch the failure accounting.

**Rule** — When a policy uses the word "consecutive", the implementation
MUST reset the counter on the non-failure event. Write the reset test
first (lesson #11: empirical probe): 2 bad, N good, 1 bad, assert still
loaded. If that test doesn't exist, "consecutive" is aspirational and
the doc should say "cumulative".

**Reference** — PR #49 review. Cf. lesson #13 (non-obvious decisions in
spec Decisions) — the decision here was "reset or not" and it wasn't
recorded explicitly.

---

## 18. Authoritative-source checks at enforcement points, not cached state

**Symptom** — PR #49 round-1's `remove()` bundled guard consulted
`self._records[name].bundled`. A bundled plugin whose load failed or
whose discovery hadn't run was absent from `_records`, so the guard
silently permitted `pip uninstall` on bundled names.

**Root cause** — Policy decisions that protect against user error
were gated on an in-memory cache whose population was conditional on
earlier lifecycle steps succeeding. When the cache wasn't populated,
the policy didn't apply.

**Rule** — At enforcement points (uninstall guards, permission
checks, allowlists, sandbox gates), re-derive the predicate from the
authoritative source — entry-point metadata, auth server, filesystem
— not from a cached record. If the cache is the source of truth, it
must be populated BEFORE any path that could bypass the enforcement.

**Reference** — PR #49 review round 1. Distinct from lesson #6
(leak-prone dicts) and lesson #13 (decision documentation) because
the failure mode is silent policy bypass, not resource leak or
lost context.

---

## 19. Author specs in the tool's format, not the protocol doc's pseudo-format

**Symptom** — PR #11 through PR #49 landed three `.spec.md` files that
were Markdown with Gherkin-ish prose. `scripts/check_specs.sh` globs
`specs/*.spec` (no `.md`), so CI silently skipped them for months.
We had a documentation promise with zero enforcement.

**Root cause** — The protocol docs (`docs/dev/agent-spec.md` and
`docs/dev/plugin-protocol.md`) described the Gherkin block informally
and used a `Test: path::name` shorthand that matches pytest node ids
but not the canonical `agent-spec` YAML-frontmatter-plus-`Test:`-block
format. Nobody ran `agent-spec init` to see the real template, so
every spec author inherited the pseudo-format from the prose example.

**Rule** — When a tool emits a canonical template (`agent-spec init`,
`ruff format`, `cargo new`, etc.), the project's own prose examples
MUST match the tool output exactly. If you can't run the tool on the
example in the doc and see it pass, the example is a trap. Lint
protocol docs against real tool output, not the other way round.

**Reference** — Issue #52, PR that ports the .spec.md files.
Generalises lesson #16 (reviewers verify pattern semantics) to
authoring: authors verify format against tool output.

---

## 20. `sqlite3 check_same_thread=False` is not a safety flag

**Symptom** — PR #59's memory_sqlite plugin set
`check_same_thread=False` to fix a Windows-only test failure
(`asyncio.to_thread` occasionally hopped worker threads). Concurrent
writes from multiple sessions silently corrupted data: 200 writes
across 200 sessions landed 117 rows with "bad parameter or other API
misuse" and "cannot start a transaction within a transaction" errors
on the rest.

**Root cause** — Python's sqlite3 `check_same_thread=True` is a
sanity check, not a concurrency primitive. Turning it off tells the
library to stop enforcing single-owner thread; the user is then
responsible for serializing access via an external mutex or a single-
worker executor. The fix silenced the warning without adding the
protection it was warning about.

**Rule** — If you set `check_same_thread=False`, pair it with one
of: (a) a dedicated `ThreadPoolExecutor(max_workers=1)` that owns
the connection, (b) an `asyncio.Lock` around every DB op, or (c)
reopen the connection per operation. (a) is preferred for
throughput; the SQL still serializes at the SQLite level but you
avoid data-race UB in the Python bindings.

**Reference** — PR #59 review round 1. Cf. lesson #18 (authoritative
enforcement, not cached state): silencing the enforcement check must
be matched by an explicit protection primitive.

---

## 21. Blanket file-level pyright pragmas silence more than they should

**Symptom** — PR #59 shipped four plugin files with file-level
`# pyright: reportUnknown*=false`. Two of the four had ZERO unknown-
type warnings without the pragma (pure cargo); two had single-line
warnings fixable with a cast; only one (llm_openai) had a genuine
SDK-surface reason. The blanket pragma means every new line added
to those files bypasses strict checking, invisibly.

**Root cause** — Silencing at the broadest scope that makes the
warning go away is easy; narrowing to exactly the offending line is
tedious. Authors reach for file-level pragmas when per-line
`# pyright: ignore[reportSpecificRule]` would have done.

**Rule** — Pyright (and mypy) pragmas MUST be scoped to the tightest
unit that makes the warning go away, in this order: per-line
`# pyright: ignore[reportRuleName]` first, then local block, then
function, then file — only if every line in the file genuinely hits
the rule. Reviewers verify the pragma's scope by removing it and
re-running the checker; if the count of warnings dropped is smaller
than the lines silenced, narrow the scope.

**Reference** — PR #59 review round 1. Applies to any diagnostic
suppression: mypy `# type: ignore[...]`, ruff `# noqa:`, pyright
`# pyright: ignore[...]`. Tighter is always better.

---

## 23. CLI flags that "will work later" must be observably inert

**Symptom** — PR #62's `yaya serve --strategy plan-execute` and
`--dev` accepted the flag and silently ignored it. The backing
plumbing (ctx.config dispatch for strategies; vite dev proxy for
the web adapter) hadn't landed. A user saw no error, assumed
their flag took effect, and wondered why the ReAct strategy still
ran.

**Root cause** — Exposing a flag in Typer/Argparse costs 4 lines
of code; wiring it to actual behavior is often blocked on a
downstream subsystem. The easy path is "accept + forget";
maintainers return to find the flag was a lie for a full release.

**Rule** — If a CLI flag is exposed but its backing behavior is
NOT fully plumbed:
1. Log a WARNING on startup naming the flag and the unfinished
   plumbing ("--strategy 'plan-execute' is not yet dispatched;
   tracked in #XYZ").
2. Reference the tracking issue in the flag's help text.
3. Add a test that asserts the warning fires.

Hiding an incomplete feature behind a silent flag violates lesson
#10 (silent no-ops). Removing the flag entirely until it works is
also acceptable — but then the spec must not advertise it either.

**Reference** — PR #62 review round 1.

---

## 25. Late-subscribe plugins miss events fired before `on_load`

**Symptom** — PR #65's web adapter subscribed to `plugin.loaded` in
`on_load`. The registry had already loaded the 4 seed plugins by the
time the adapter's `on_load` ran; those `plugin.loaded` events had
already been delivered, so the adapter's `_plugin_rows` stayed
empty. Users saw an incomplete plugin list in the UI despite the
kernel CLI listing all 5 plugins.

**Root cause** — The bus does not retain events for late subscribers.
Any plugin that publishes state during startup (registry emitting
`plugin.loaded` per plugin as they load) is invisible to plugins
loaded after.

**Rule** — Plugins that care about startup state must NOT rely on
the event stream alone. Options:
1. **Eager snapshot at on_load**: enumerate the underlying source
   (entry points, filesystem state, stored config) directly during
   `on_load`, then merge subsequent events as deltas. PR #65 took
   this path.
2. **Late-join replay**: registries can snapshot-and-replay on
   subscribe (requires registry-side support; bus doesn't replay).
3. **Bootstrap event**: the kernel emits a single `kernel.ready`
   event after all initial load events, giving late subscribers a
   signal to query a fresh snapshot. yaya already emits
   `kernel.ready`; adapters can use it as a "prime your caches now"
   trigger in addition to — not instead of — the eager snapshot.

Avoid spinning up your own event-retention buffer in plugins — that
is what a durable bus would be, and yaya's bus deliberately isn't.

**Reference** — PR #65 review round 1. Distinct from lessons #6
(leak-prone dicts) and #15 (missing correlation field): this is
specifically about startup-race event loss.

---

## 27. Framework modules pass the Dependency Rule only if their assumptions align with your use-case direction

**Symptom** — pi-web-ui ships a complete UI library. The tempting
path is "use everything"; the architectural path is "use only the
exports that don't drag in a conflicting use case." Several
pi-web-ui exports assume the browser owns the LLM-calling agent,
the user's API keys, and conversation state. yaya's architecture
inverts each one: Python kernel owns the agent, env vars hold keys,
a future Python memory plugin holds sessions. Importing any of
those modules would have dragged pi-web-ui's use-case assumptions
into yaya's inner rings.

**Root cause** — "Framework ring" is a direction, not a folder. A
framework-adjacent library can still contain USE-CASE-level
assumptions disguised as components. The Dependency Rule demands
that we evaluate EACH export, not the library as a unit.

**Rule** — When adopting a framework library, enumerate its
exports and classify each by the USE CASE it assumes:

1. **Pure presentation** (markdown rendering, button styles,
   message bubbles, streaming containers): framework-ring, import
   freely.
2. **Use-case-coupled** (agent loop, API-key storage, provider
   routing, browser-side session persistence): blacklisted if the
   assumed use case differs from yours. "Defer" is the wrong
   disposition — if architectural, say "never".
3. **Pragmatic boundaries** (localStorage for theme preference):
   framework-ring-ok because the choice is trivially replaceable.

Publish the whitelist AND blacklist in the subpackage's AGENT.md
so future agents don't relitigate. Enforce with a pre-commit grep.

**Reference** — PR for issue #66 (real pi-web-ui integration).
Companion to lesson #19 (author in tool format): here we author
imports against architectural constraints, not available
components.

---

## 28. Verify squash-merge ingested the latest push before cleanup

**Symptom** — PR #65's round-2 fix commit `fb2f9bd` ("populate version
from ep.dist.version; lesson #26") was pushed to the branch,
verified green in CI, then `gh pr merge --squash --admin` was
called. The squash commit `2d83483` that landed on `main` did NOT
contain that change — it regressed to the round-1 blank-version
form. `git branch --contains fb2f9bd` returns empty; the commit is
orphaned. The lesson #26 fix reached production only two days later
via PR #68 after the regression was spotted during PR #67's live
smoke.

**Root cause** — `gh pr merge --admin` resolved the squash against a
stale head-of-branch because the merge API call raced the final
`git push`. The symptom showed up at merge time as
`failed to run git: fatal: 'main' is already used by worktree at
.../yaya` — which I treated as cosmetic. It wasn't: GitHub had
already taken the merge-base reading from the earlier SHA and
silently dropped the in-flight commit.

**Rule** — Before `gh pr merge --admin`, **verify the head matches
what you expect** with `gh pr view <N> --json headRefOid`. After
merge, **verify the merge commit contains the intended change**
with `git log origin/main -1 -- <changed file>` or
`git diff origin/main~1 origin/main -- <file>`. Any "already used
by worktree" warning from `gh pr merge` is NOT benign — it hints at
a non-atomic merge path. Never dismiss it.

For the subagent workflow: a subagent's "CI green; ready to merge"
report is not a substitute for verifying the merge ACTUALLY
ingested the expected SHA. After every merge, grep the merged
source for one specific string from the latest fix to confirm it
landed.

**Reference** — PR #68 (restore), orphaned commit `fb2f9bd`, merged
PR #65 as `2d83483`. Related to lesson #22 (long-lived branches
accumulate ghost deletions in PR diffs) — both are classes of
"merge-time diff ≠ what you thought" hazards. Cf. lesson #11
(empirical probes): verify AT the moment of merge, not when code
was reviewed.

---

## 29. Subclass `pre_approve` exceptions silently orphan tool calls

**Symptom** — A `Tool` subclass overrides `pre_approve` and raises
something other than `ApprovalCancelledError` / `ToolRejectedError`
(say a `KeyError` from a custom allowlist lookup, or a stray
`AssertionError` from a defensive check). The dispatcher's narrow
`except` only catches the two sentinel errors, so the unexpected
exception propagates back to the bus, surfaces as a `plugin.error`
with `source="kernel"`, hits the bus's recursion guard
(`_report_handler_failure` short-circuits kernel-source failures),
and gets dropped. The originating `tool.call.request` is now an
orphan: no `tool.call.result`, no `tool.error`, and the agent loop
hangs on the unresolved future until the entire kernel shuts down.

**Root cause** — Any checkpoint inserted **between** a request event
and its terminal result event must translate ALL unexpected
exceptions to a terminal event. Catching only the "expected" sentinel
types is brittle: subclasses, third-party tools, and even stdlib
calls inside the checkpoint can raise something the dispatcher never
imagined, and once the failure escapes the checkpoint the request
event has nowhere to land.

**Rule** — Wrap every pre-`run` checkpoint in `except Exception`
(NOT `BaseException`, so cooperative cancellation still works), log
with `_logger.exception(...)`, and emit a terminal `tool.error` so
the caller's future resolves. The same rule applies to any future
checkpoint in the same dispatch path (rate limit, capability check,
sandbox setup, ...).

**Reference** — PR #83 review finding P2-2.
`src/yaya/kernel/tool.py::dispatch` (the `if tool.requires_approval:`
block). Test:
`tests/kernel/test_approval.py::test_pre_approve_crash_translates_to_tool_error`.
Related to lesson #15 (silent "no request_id" drops hang plugin
authors) — same family of "request lost without trace" hazards.

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
