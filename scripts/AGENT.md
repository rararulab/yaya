# scripts — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Release-time and CI helper scripts. Not imported by the package.

## External Reality
- Scripts run in CI (release-please workflow) and locally via `just`. Exit codes are their contract.
- `check_version_tag.py` gates releases — a mismatch between the git tag and `pyproject.toml` version fails the release pipeline.

## Constraints
- `check_version_tag.py` — verifies the git tag matches `pyproject.toml` version at release time.
- `check_banned_frameworks.py` — enforces AGENT.md §4 (no third-party agent frameworks). Scans `pyproject.toml` declared deps and AST imports under `src/` + `tests/` against a hardcoded ban list. Stdlib only. Takes `--json` for CI integration. Wired into pre-commit + the `Lint & type check` CI job.
- Scripts are **standalone**: runnable with `python scripts/<name>.py` using only stdlib (or clearly documented deps available in the CI environment).
- No imports from `yaya.*` unless strictly required — scripts may run before install in CI.
- Every script has a module-level docstring stating: what it does, when it runs, exit-code semantics.

## Interaction (patterns)
- Do NOT add scripts that duplicate `justfile` recipes — extend the justfile instead.
- Do NOT hard-code secrets or environment-specific paths — read from env.
- Do NOT add a script without a caller (justfile recipe or workflow step). Orphans rot.

## Budget & Loading
- Release flow: [../docs/dev/release.md](../docs/dev/release.md).
