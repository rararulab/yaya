# Testing

- `just test` — pytest with coverage (terminal report + missing lines).
- `just check` — ruff lint + format check + `mypy --strict`.
- `just check-all` — `check` + lock-file consistency + all pre-commit hooks (CI parity).

## Rules

- **No public function/class without a test.** Coverage must not regress on any PR.
- **Coverage floor: 80%.** Enforced by `fail_under = 80` in `pyproject.toml`.
  Raise the floor after every merge that meaningfully improves coverage.
- **Prefer integration over mocks.** Reach for real objects, `tmp_path`, and
  recorded fixtures before `unittest.mock`.
- **Tests must fail before they pass.** Write the failing test first, then
  the implementation (TDD).
- **Feature PRs**: each observable behavior is covered by a BDD scenario
  in `specs/<slug>.spec.md` whose `Test:` selector names the test
  function. `agent-spec guard` rejects unbound scenarios. See
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
- **Snapshot tests (syrupy)**: pin the exact shape of a stable output
  (`--help`, JSON payload). Refresh deliberately, not reflexively.
- **CLI tests**: `runner.invoke(cli_app, [...])` + assert exit code, JSON
  shape, stderr vs stdout routing. See [cli.md](cli.md).

## Isolation fixtures

Two autouse fixtures in `tests/conftest.py`:

- `_isolate_state_dir` — redirects `XDG_DATA_HOME` into `tmp_path` so the
  updater's state files never touch `~/.local`.
- `_no_auto_update` — sets `YAYA_NO_AUTO_UPDATE=1` so the toast stays
  silent in CLI tests (override in a specific test if you need it).

## Pre-commit

Pre-commit hooks run on every commit. **Never** use `--no-verify`. If a hook
fails, fix the cause before the final commit of the PR.
