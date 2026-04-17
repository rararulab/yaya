# src/yaya/cli/commands — Agent Guidelines

## Purpose
One file per subcommand. Each exports `register(app: typer.Typer) -> None`.

## Architecture
Current commands:
- `hello.py` — trivial smoke command; good template for new commands.
- `version.py` — prints `__version__`; also available via `--version` flag.
- `update.py` — self-update flow; delegates to `yaya.core.updater`.

## Critical Invariants
- Each module MUST expose `register(app)` and nothing else at top-level side-effect-wise.
- Command body pattern:
  1. Parse args (Typer options).
  2. Build a result by calling `yaya.core.*` (pure).
  3. Render via `emit_ok` / `emit_error` from `yaya.cli.output`.
  4. Exit non-zero on failure.
- Every command's `--help` has an `epilog=` with at least one runnable example.
- Destructive commands require `--dry-run` and `--yes`.

## What NOT To Do
- Do NOT put business logic here — it belongs in `yaya.core.*`.
- Do NOT call `print` / `typer.echo` / `Console().print`.
- Do NOT add a command without a test in `tests/cli/test_<name>.py` covering text mode, `--json`, and at least one failure path.
- Do NOT forget to register the new module in `../__init__.py`.

## Extending — see checklist
[../../../../docs/dev/cli.md](../../../../docs/dev/cli.md) has the full "adding a command" checklist. Follow it exactly.

## Dependencies
- `yaya.cli` (app, `CLIState`, `output.*`), `yaya.core.*`.
