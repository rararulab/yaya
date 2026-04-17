spec: task
name: "harness-agent-spec"
tags: [harness, governance]
---

## Intent

Prove the repository actually runs `agent-spec` in CI and pre-commit,
not just in documentation. This spec is deliberately minimal: its
only job is to bind a scenario to an already-passing test so
`agent-spec lifecycle --layers lint,boundary` has a real target. If
this file disappears, the harness is back to being a documentation
promise.

## Decisions

- Seed spec path is `specs/harness-agent-spec.spec`.
- It binds to post-install smoke tests in `tests/e2e/test_cli_smoke.py`
  so enabling the spec does not create new test work.
- `agent-spec` CLI version pin: `0.2.7` (see `docs/dev/agent-spec.md`).
- CI layer selection: `lint,boundary` only; the `verify` layer needs
  an AI backend that is out of scope for this harness.

## Boundaries

### Allowed Changes
- `specs/harness-agent-spec.spec`
- `specs/*.spec`
- `docs/dev/agent-spec.md`
- `.pre-commit-config.yaml`
- `.github/workflows/main.yml`
- `.github/actions/setup-python-env/action.yml`
- `justfile`

### Forbidden
- `src/yaya/**`
- `pyproject.toml`
- every other test or doc file

## Completion Criteria

Scenario: yaya version exits zero after wheel install
  Test:
    Package: yaya
    Filter: tests/e2e/test_cli_smoke.py::test_version_exits_zero
  Level: e2e
  Given the wheel was installed into a fresh venv
  When the user runs `yaya version`
  Then the process exits 0 with a non-empty stdout

Scenario: yaya --json version emits the canonical shape
  Test:
    Package: yaya
    Filter: tests/e2e/test_cli_smoke.py::test_version_json_shape
  Level: e2e
  Given the installed yaya
  When the user runs `yaya --json version`
  Then stdout is a JSON object with ok=true, action="version", and a string version field

Scenario: unknown command exits non-zero
  Test:
    Package: yaya
    Filter: tests/e2e/test_cli_smoke.py::test_unknown_command_fails_with_nonzero
  Level: e2e
  Given the installed yaya
  When the user runs an unrecognized subcommand
  Then the process exits with a non-zero code

## Out of Scope

- Writing specs retroactively for already-merged features.
- Any enforcement beyond `lint,boundary`; full `verify` requires an
  AI backend and is tracked separately.
