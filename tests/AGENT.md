# tests — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Pytest suite. Mirrors `src/yaya/` one-to-one. Tests are the specification.

## External Reality
- `just test` (pytest + coverage) is ground truth. Coverage floor: 80% (`fail_under`).
- `addopts` enforces `--strict-markers` + `--strict-config` + 30s timeout.
- `pytest-randomly` shuffles test order — order-dependence fails CI immediately.
- Tests run hermetically: autouse fixtures redirect `STATE_DIR` and disable the update toast.

## Constraints
- `conftest.py` shared fixtures:
  - `runner` — Typer `CliRunner`.
  - `cli_app` — the configured Typer app.
  - `_isolate_state_dir` (autouse) — redirects `STATE_DIR` under `tmp_path`.
  - `_no_auto_update` (autouse) — sets `YAYA_NO_AUTO_UPDATE=1`.
  - `project_root` — path to repo root.
- `cli/` — CLI-level tests (one file per command).
- `core/` — unit tests per domain module.
- File layout MIRRORS source: `src/yaya/core/foo.py` ⇒ `tests/core/test_foo.py`.
- Markers: `unit` (default, fast/pure), `integration` (fs/subprocess/local net), `slow` (>1s).

## Interaction (patterns)
- TDD: the test must fail before the implementation makes it pass.
- Prefer real objects + `tmp_path` + `pytest-httpx` over `unittest.mock`.
- Do NOT hit real network, real filesystem outside `tmp_path`, or real wall-clock time without a seam.
- Do NOT mark tests `skip` / `xfail` without a linked issue.
- Every non-trivial change ships with a `specs/<slug>.spec.md` whose scenarios bind to test functions via `Test:` selectors — `agent-spec guard` rejects unbound scenarios.
- New public function/class ⇒ at least one test; coverage must not regress.

## Budget & Loading
- Commands + conventions: [../docs/dev/testing.md](../docs/dev/testing.md).
- Contract workflow + scenario shape: [../docs/dev/agent-spec.md](../docs/dev/agent-spec.md).
