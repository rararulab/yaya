# Testing

- `just test` — pytest with coverage (terminal report + missing lines).
- `just check` — ruff lint + format check + `mypy --strict`.
- `just check-all` — `check` + lock-file consistency + all pre-commit hooks (CI parity).

## Rules

- **No public function/class without a test.** Coverage must not regress on any PR.
- **Prefer integration over mocks.** Reach for real objects, `tmp_path`, and
  recorded fixtures before `unittest.mock`.
- **Tests must fail before they pass.** Write the failing test first, then
  the implementation (TDD).
- **Agent/flow PRs**: include an Agent Spec round-trip test
  (see [agent-spec.md](agent-spec.md)).
- **Test layout mirrors `src/`.** A file at `src/yaya/core/foo.py` has its
  tests at `tests/core/test_foo.py`.
- **No network, no time, no randomness** in tests without an explicit seam.

## Pre-commit

Pre-commit hooks run on every commit. **Never** use `--no-verify`. If a hook
fails, fix the cause before the final commit of the PR.
