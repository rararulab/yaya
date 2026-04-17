# tests — Agent Guidelines

## Purpose
Pytest suite. Mirrors `src/yaya/` one-to-one.

## Architecture
- `conftest.py` — shared fixtures:
  - `runner`: Typer `CliRunner`.
  - `cli_app`: the configured Typer app.
  - `_isolate_state_dir` (autouse): redirects `STATE_DIR` under `tmp_path` so tests never touch `~/.local/share/yaya`.
  - `_no_auto_update` (autouse): sets `YAYA_NO_AUTO_UPDATE=1` to suppress the startup toast.
  - `project_root`: path to repo root.
- `cli/` — CLI-level tests (one file per command).
- `core/` — unit tests for domain modules.

## Critical Invariants
- Test file layout MIRRORS source: `src/yaya/core/foo.py` ⇒ `tests/core/test_foo.py`.
- Every public function/class in `src/yaya/` has at least one test. Coverage must not regress (`fail_under = 80`).
- **No network, no time, no randomness** without an explicit seam. Use `pytest-httpx` for HTTP, `freezegun` or injected clocks for time.
- New agents/flows in `core/` MUST have an Agent Spec round-trip test in `tests/core/`.
- Tests must fail before they pass (write the failing test first).

## What NOT To Do
- Do NOT mock what `pytest-httpx` or `tmp_path` can cover with real objects.
- Do NOT hit the real filesystem outside `tmp_path` — the autouse fixtures exist for this.
- Do NOT mark tests `skip`/`xfail` without a linked issue.

## Markers
- `unit` — default; fast, pure.
- `integration` — touches filesystem, subprocess, local network.
- `slow` — >1s; run via `pytest -m slow` or skipped via `-m 'not slow'`.

## Dependencies
See [../docs/dev/testing.md](../docs/dev/testing.md) for commands, conventions, and the TDD workflow.
