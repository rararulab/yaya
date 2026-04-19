# Testing

- `just test` — pytest with coverage (terminal report + missing lines).
- `just check` — ruff lint + format check + `mypy --strict`.
- `just check-all` — `check` + lock-file consistency + all pre-commit hooks (CI parity).

## Rules

- **No public function/class without a test.** Coverage must not regress on any PR.
- **Global coverage floor: 90%.** Enforced by `fail_under = 90` in
  `pyproject.toml` (applied by `pytest-cov` against combined line +
  branch coverage).
- **Per-module gates** enforced by `scripts/check_coverage.py` against
  the `coverage.xml` that `pytest --cov-report=xml` produces.
  The script runs locally via `just test` and in CI after the test
  step on every OS in the matrix, so platform-specific regressions
  surface at PR time. Current gates (line coverage):

  | Prefix | Gate | Aspirational target |
  | --- | --- | --- |
  | `<global>` (line + branch via `fail_under`) | 90.0% | rise with reality |
  | `src/yaya/kernel/` | 93.5% | 95% |
  | `src/yaya/core/` | 90.0% | 90% |
  | `src/yaya/cli/` | 85.0% | 85% |
  | `src/yaya/plugins/agent_tool/` | 95.0% | 95% |
  | `src/yaya/plugins/llm_echo/` | 95.0% | 95% |
  | `src/yaya/plugins/llm_openai/` | 79.5% | 85% |
  | `src/yaya/plugins/mcp_bridge/` | 90.0% | 90% |
  | `src/yaya/plugins/memory_sqlite/` | 89.5% | 90% |
  | `src/yaya/plugins/strategy_react/` | 95.0% | 95% |
  | `src/yaya/plugins/tool_bash/` | 95.0% | 95% |
  | `src/yaya/plugins/web/` | 84.0% | 85% |

  Gates tagged below their aspirational target track the ratchet
  convention *"reality minus 0.5"* — they rise as coverage improves
  and never drop without a `governance` issue. The tables in
  `scripts/check_coverage.py` are the source of truth; the table here
  is a mirror for readers who are not reading the script.

### Ratchet policy

1. After any merge to `main` that raises a prefix's coverage above
   `gate + 0.5`, bump its gate in `scripts/check_coverage.py` (and the
   global `fail_under` in `pyproject.toml`) to `floor(actual − 0.5)`
   rounded down to the nearest 0.5. Do this in the same PR or an
   immediate follow-up.
2. Gates **never retreat**. Lowering a gate — or relaxing the global
   floor — requires a dedicated issue labelled `governance` and owner
   sign-off.
3. If current reality is below an aspirational target (e.g.
   `plugins/web/` below 85%), the gate sits at *reality minus 0.5*;
   a follow-up issue raises coverage first, then the gate. Never set
   a gate you cannot meet today.
4. The `ratchet->` column in `check_coverage.py`'s output names the
   value a gate could be raised to right now. Watch for it in PR
   logs — free ratchet opportunities are ratchet opportunities missed.
- **Prefer integration over mocks.** Reach for real objects, `tmp_path`, and
  recorded fixtures before `unittest.mock`.
- **Tests must fail before they pass.** Write the failing test first, then
  the implementation (TDD).
- **Feature PRs**: each observable behavior is covered by a BDD scenario
  in `specs/<slug>.spec` whose `Test:` selector names the test
  function. `scripts/check_specs.sh` runs `agent-spec lifecycle`, and
  pytest-bdd rejects unbound `.feature` scenarios. See
  [agent-spec.md](agent-spec.md).
- **Test layout mirrors `src/`.** A file at `src/yaya/core/foo.py` has its
  tests at `tests/core/test_foo.py`.
- **No network, no time, no randomness** in tests without an explicit seam.
- **Every test finishes in ≤30s.** Enforced by `pytest-timeout`. If you need
  longer, mark the test `@pytest.mark.slow` and justify it in the PR body.
- **Tests must pass in random order.** `pytest-randomly` shuffles every run.
  Order-dependent tests are bugs.

## Stack

- `pytest` + `pytest-asyncio` (auto mode).
- `pytest-cov` — branch coverage, threshold enforced.
- `pytest-httpx` — mock httpx calls; **the only** way to touch the network
  in tests.
- `pytest-timeout` — 30s default per test.
- `pytest-randomly` — shuffled execution order.
- `hypothesis` — property-based tests for pure logic
  (see `tests/core/test_updater_properties.py`).
- `syrupy` — snapshot tests for stable outputs like `--help`.
  Refresh with `pytest --snapshot-update`; commit the resulting
  `__snapshots__/` changes.
- `typer.testing.CliRunner` (via conftest `runner` fixture) — CLI end-to-end.

## Markers

Registered in `pyproject.toml`:

| Marker | Meaning | Default |
|---|---|---|
| `unit` | fast, isolated, pure logic | implicit default |
| `integration` | touches fs / subprocess / local network | explicit |
| `slow` | >1s; skippable via `-m "not slow"` | explicit |

Use module-level `pytestmark = pytest.mark.unit` to tag a whole file.
`--strict-markers` fails the suite on typos.

## Test kinds (when to reach for each)

- **Example tests**: one concrete input → one expected output. Default.
- **Property tests (hypothesis)**: assert an invariant over a *generated*
  input space. Use for pure functions (parsers, comparators, transforms).
- **Snapshot tests (syrupy)**: pin the exact shape of a stable output.
  Only snapshot outputs that are platform-agnostic — e.g. a JSON
  payload from `yaya --json foo`. Do NOT snapshot rich-rendered help
  text; rich picks different box-drawing glyphs on Windows vs POSIX,
  and snapshots drift. Refresh deliberately, not reflexively.
- **CLI tests**: `runner.invoke(cli_app, [...])` + assert exit code, JSON
  shape, stderr vs stdout routing. See [cli.md](cli.md).
- **BDD tests (pytest-bdd)**: Gherkin scenarios in
  `tests/bdd/features/*.feature` bound to step definitions in
  `tests/bdd/test_*.py`. Each scenario in `specs/<slug>.spec` Completion
  Criteria has a matching `.feature` scenario; changing the scenario text
  without updating the step definition causes pytest to fail with
  `StepDefinitionNotFoundError`. `scripts/check_feature_sync.py` verifies
  `.feature` and `.spec` stay aligned; it runs in `just check` and CI.
  Step-by-step conversion procedure lives in
  [bdd-workflow.md](bdd-workflow.md) — new agents authoring or
  migrating a spec read that first. See [agent-spec.md](agent-spec.md)
  for how `.spec` authoring and BDD execution relate.

## Isolation fixtures

Two autouse fixtures in `tests/conftest.py`:

- `_isolate_state_dir` — redirects `XDG_DATA_HOME` into `tmp_path` so the
  updater's state files never touch `~/.local`.
- `_no_auto_update` — sets `YAYA_NO_AUTO_UPDATE=1` so the toast stays
  silent in CLI tests (override in a specific test if you need it).

## Pre-commit

Pre-commit hooks run on every commit. **Never** use `--no-verify`. If a hook
fails, fix the cause before the final commit of the PR.
