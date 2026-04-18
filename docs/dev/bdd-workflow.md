# BDD workflow — from `.spec` to executable Gherkin

How an agent (human or AI) takes a `specs/<slug>.spec` contract and
lands real BDD-backed tests that fail on scenario-text drift. This is
the complete playbook; new agents should follow it step-by-step and not
improvise.

## When to use this workflow

- **Every new feature PR.** If you land a new `specs/<slug>.spec`,
  land the matching `.feature` + step definitions **in the same PR**.
  The `agent-spec lifecycle` + `check_feature_sync.py` gates will
  otherwise fail on the spec author's next merge.
- **Backfilling an existing spec.** If a `.spec` predates this
  workflow, convert it in one focused PR. Do not mix conversion with
  feature work.

## What BDD means here

- `specs/<slug>.spec` — authoritative human-facing contract. Authored
  with agent-spec's YAML-frontmatter + Completion Criteria sections.
- `tests/bdd/features/<slug>.feature` — executable Gherkin mirror of
  the spec's Completion Criteria. One scenario per `.spec` scenario,
  same ordering, same Given/When/Then wording (minus agent-spec's
  `Test:` / `Package:` / `Filter:` / `Level:` metadata lines).
- `tests/bdd/test_<slug>.py` — pytest-bdd step definitions that wire
  each Given/When/Then to real production code. pytest-bdd fails
  collection with `StepDefinitionNotFoundError` if any scenario step
  lacks a matching `@given` / `@when` / `@then`, so drift between
  scenario text and code is impossible to commit quietly.
- `tests/bdd/conftest.py` — shared fixtures (BDDContext, event loop,
  common Given steps).
- `scripts/check_feature_sync.py` — fails CI when `.spec` and
  `.feature` scenario names or step texts drift.

## File locations cheat sheet

```
specs/<slug>.spec                              # author
tests/bdd/features/<slug>.feature              # mirror of spec scenarios
tests/bdd/test_<module>.py                     # step definitions
tests/bdd/conftest.py                          # reuse existing fixtures; add shared steps here
```

The feature-file stem must equal the spec stem: the sync check pairs
them by filename. `tests/bdd/test_<module>.py` name is free; use the
kernel module being exercised.

## The procedure

### 1. Read the `.spec` Completion Criteria

Open `specs/<slug>.spec`. Skim the Intent and Decisions for context.
The Completion Criteria section lists every scenario. You are going to
translate that block verbatim (minus metadata) into a `.feature`.

### 2. Create `tests/bdd/features/<slug>.feature`

Template:

```gherkin
Feature: <One-line summary of the capability>

  <One-paragraph narrative. Usually paraphrases the spec Intent in
  business-readable prose.>

  Scenario: <copy the first `.spec` Scenario: line verbatim>
    Given <copy the spec's Given verbatim>
    And <copy And>
    When <copy the spec's When verbatim>
    Then <copy the spec's Then verbatim>

  # Repeat for every scenario in the .spec Completion Criteria,
  # preserving order.
```

**Drop entirely**: `Test:`, `Package:`, `Filter:`, `Level:` lines.
They are agent-spec authoring metadata; pytest-bdd does not read them
and the sync check strips them on both sides.

**Preserve exactly**: scenario names, step prefixes (Given/When/Then/And),
and step text wording. `scripts/check_feature_sync.py` compares
string-for-string.

### 3. Write `tests/bdd/test_<module>.py`

Template:

```python
"""Pytest-bdd execution of specs/<slug>.spec scenarios.

The Gherkin text in ``features/<slug>.feature`` is the authoritative
BDD contract for <subsystem>. Each scenario binds to step definitions
in this module via pytest-bdd; changing the scenario text without a
matching step def causes pytest to fail with
``StepDefinitionNotFoundError``.

This complements (does not replace) the engineering-level tests in
``tests/<area>/test_<module>.py``. BDD here proves the scenarios the
spec advertises are actually executed; the pytest unit tests cover
edge cases and internals not worth surfacing in Gherkin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# Production-code imports only. Step defs wire Gherkin text to the real
# API; they do NOT import test helpers from tests/<area>/*.
from yaya.kernel.<module> import <Type>

from .conftest import BDDContext

pytestmark = pytest.mark.unit  # BDD tests run in the default unit suite

FEATURE_FILE = Path(__file__).parent / "features" / "<slug>.feature"
scenarios(str(FEATURE_FILE))


# -- Scenario N: <paraphrase the scenario name> ---------------------------

@given("<exact Given text from the .feature>")
def _some_precondition(ctx: BDDContext) -> None:
    ...

@when("<exact When text from the .feature>")
def _some_action(ctx: BDDContext, loop: asyncio.AbstractEventLoop) -> None:
    # ``loop`` fixture is defined in conftest.py — per-scenario event
    # loop so async publishes do not leak between scenarios.
    assert ctx.bus is not None
    loop.run_until_complete(ctx.bus.publish(...))

@then("<exact Then text from the .feature>")
def _assert_outcome(ctx: BDDContext) -> None:
    ...
```

### 4. Step definition patterns

#### Pattern A: literal text match

```python
@given("a running EventBus")
def _a_running_event_bus(ctx: BDDContext) -> None:
    ctx.bus = EventBus()
```

#### Pattern B: capture a value via `parsers.re`

```python
@given(
    parsers.re(r'a subscriber registered for "(?P<kind>[\w.]+)"$'),
    target_fixture="_kind",
)
def _subscriber_for(ctx: BDDContext, kind: str) -> str:
    ...
    return kind
```

Use `parsers.re` when the scenario text contains a value the step uses
(event kinds, config ints, payload keys). The named capture group
becomes a step parameter; declare it in the function signature.

#### Pattern C: async step that publishes on the bus

```python
@when(parsers.re(r'a "(?P<kind>[\w.]+)" event is published$'))
def _publish(ctx: BDDContext, kind: str, loop: asyncio.AbstractEventLoop) -> None:
    assert ctx.bus is not None
    ev = new_event(kind, {"text": "hi"}, session_id="bdd", source="adapter")
    ctx.published_events.append(ev)
    loop.run_until_complete(ctx.bus.publish(ev))
```

Any step that awaits a coroutine takes the ``loop`` fixture and calls
``loop.run_until_complete(...)``. pytest-bdd step defs are synchronous
functions; **do not** use ``async def`` for them (pytest-bdd does not
await them).

#### Pattern D: shared precondition moved to conftest

If more than one feature file needs the same `@given`, move it to
`tests/bdd/conftest.py` so the step is discovered by every test module
that imports from there. Example: `@given("a running EventBus")` is in
conftest because every kernel feature file uses it.

### 5. BDDContext usage

`tests/bdd/conftest.py` provides `BDDContext`, a scenario-scoped
dataclass for step-to-step state transfer:

```python
@dataclass
class BDDContext:
    bus: EventBus | None = None
    received: dict[str, list[Event]] = ...
    errors: list[Event] = ...
    handlers: dict[str, Callable[[Event], Awaitable[None]]] = ...
    published_events: list[Event] = ...
    publish_error: Exception | None = None
    extras: dict[str, Any] = ...
```

- Use named fields when the concept is durable (`bus`, `received`).
- Use `extras` for scenario-specific scratch state (a captured
  exception, a counter, an intermediate value).
- Fixtures reset the ctx per scenario automatically; never put shared
  state in module globals.

### 6. Verify locally

```bash
# Every scenario collects and passes
uv run python -m pytest tests/bdd/test_<module>.py -v

# .feature text matches .spec text
uv run python scripts/check_feature_sync.py

# Full gate
just check && just test
```

### 7. Commit

One PR, one spec's worth of conversion. Commit message pattern:

```
feat(bdd): <slug> scenarios via pytest-bdd

- Add tests/bdd/features/<slug>.feature mirroring
  specs/<slug>.spec Completion Criteria (<N> scenarios)
- Add tests/bdd/test_<module>.py with step defs wired to the real
  <subsystem> API
- <any new shared steps added to tests/bdd/conftest.py>

Closes: makes specs/<slug>.spec scenarios drift-proof — changing the
Gherkin text without updating a step def breaks pytest collection.

Closes #<issue>

Co-Authored-By: <you>
```

## Common pitfalls

- **Wrong fixture name.** `ctx` and `loop` are defined in
  `tests/bdd/conftest.py`. Misspell either and pytest-bdd silently
  fails to inject and the step crashes at runtime. Keep the names.
- **`async def` step defs.** pytest-bdd does not await them. Use
  synchronous functions + `loop.run_until_complete(...)`.
- **Copy-paste with formatting changes.** `check_feature_sync.py`
  compares exact step text. "a running EventBus" and "an running
  EventBus" are different scenarios to the checker.
- **Forgetting `scenarios(str(FEATURE_FILE))`.** Without this call,
  pytest-bdd does not bind any scenario to the step defs in the module;
  the test file passes trivially without running anything.
- **Reusing a step def name.** Python shadowing rules apply — two
  `def _handler(...)` at module level collide. pytest-bdd uses the
  registered Gherkin text, not the function name, so prefix step-def
  functions with `_` (e.g. `_a_running_event_bus`) to avoid accidental
  collisions with fixture names.
- **Mixing BDD with existing unit test patterns.** BDD and unit tests
  live side-by-side. BDD is the scenario contract; unit tests cover
  edge cases that are not worth surfacing in Gherkin. Do not delete
  the existing `tests/<area>/test_<module>.py` when converting the
  spec — they remain the engineering verification.

## Verification checklist

Before opening a PR:

- [ ] `tests/bdd/features/<slug>.feature` exists and has the same
      scenario names (in order) as `specs/<slug>.spec`.
- [ ] `uv run python scripts/check_feature_sync.py` exits 0.
- [ ] `uv run python -m pytest tests/bdd/test_<module>.py -v` exits 0
      and the scenario count matches the spec.
- [ ] `just check && just test` green.
- [ ] No new step defs duplicate an existing one in `conftest.py` — if
      overlap exists, consolidate to `conftest.py`.
- [ ] Step defs import **only** production code from `src/yaya/…`,
      never from `tests/<area>/`.
- [ ] Commit message follows the project's Conventional Commits style
      (≤50-char title, body bullets starting with imperative verbs,
      closing trailer).

## See also

- [`docs/dev/agent-spec.md`](agent-spec.md) — how `.spec` files are
  authored and what `agent-spec lifecycle` enforces.
- [`docs/dev/testing.md`](testing.md) — the broader test harness (unit,
  property, snapshot, e2e) that BDD slots into.
- [`docs/dev/workflow.md`](workflow.md) — issue → worktree → PR state
  machine; BDD conversion happens inside a worktree for one issue.
- [`specs/kernel-bus-and-abi.spec`](../../specs/kernel-bus-and-abi.spec)
  and [`tests/bdd/test_kernel_bus.py`](../../tests/bdd/test_kernel_bus.py)
  — reference conversion to copy the structure from.
