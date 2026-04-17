# CLI Conventions

Canonical spec: `rararulab/.github/docs/agent-friendly-cli.md`. yaya follows
it strictly — this file only records yaya-specific mechanics.

## Pattern

Every subcommand:

1. Builds a result in `core/` (pure logic, no printing, no `sys.exit`).
2. Calls `emit_ok` / `emit_error` from `yaya.cli.output` — never `print`,
   `typer.echo`, or `Console().print` directly.
3. Exits non-zero on failure; `fatal()` is the one-liner helper.

## JSON mode (`--json`)

- Stdout carries a single `{"ok": bool, ...}` object. Canonical shape
  enforced by `cli/output.py`.
- Success: `{"ok": true, "action": "<verb>", ...data}`.
- Error: `{"ok": false, "error": "...", "suggestion": "...", ...data}`.
- Human logs and warnings go to **stderr** (`warn(...)`).

## Extending the CLI — checklist

When adding a new command:

- [ ] Create `src/yaya/cli/commands/<name>.py` exposing `register(app)`.
- [ ] Register it in `src/yaya/cli/__init__.py`.
- [ ] Body pattern: build a structured result in `core/`, then
      `emit_ok` / `emit_error`. No direct printing.
- [ ] Add `epilog=EXAMPLES` with at least one runnable example.
- [ ] Destructive? Add `--dry-run` and `--yes`.
- [ ] Error paths must emit a `suggestion` and exit non-zero.
- [ ] Add `tests/cli/test_<name>.py` covering text mode, `--json`, and at
      least one failure path (exit code + JSON shape).
- [ ] Network/disk work: add `tests/core/test_<module>.py` with
      `pytest-httpx` mocks — no real network in tests.
