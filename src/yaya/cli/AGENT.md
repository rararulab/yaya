# src/yaya/cli — Agent Guidelines

## Purpose
Typer-based CLI. Parses args, delegates to `core/`, renders results through `output.py`.

## Architecture
- `__init__.py` — builds the Typer `app`, defines `CLIState`, wires the root callback (`--json`, `-v`, `-q`, `--version`). Each command module calls `register(app)` here.
- `output.py` — canonical renderers: `emit_ok`, `emit_error`, `warn`, `fatal`. JSON shape: `{"ok": bool, ...}`.
- `commands/` — one file per subcommand. See [commands/AGENT.md](commands/AGENT.md).

## Critical Invariants
- CLI is a **thin shell**: no business logic here, only arg parsing + delegation + rendering.
- Every command uses `emit_ok` / `emit_error` — never `print`, `typer.echo`, or `Console().print` directly (except `__init__.py` for `--help` / `--version` short-circuits).
- `CLIState` is attached to `ctx.obj` in the root callback. Commands read it via `ctx.obj`, never via module globals.
- Errors set non-zero exit (`raise typer.Exit(code=N)` or `fatal(...)`).

## What NOT To Do
- Do NOT import from `yaya.core.*` at module top level inside command files when avoidable — defer to function body to keep `--help` fast.
- Do NOT add a command without updating [commands/AGENT.md](commands/AGENT.md) and [../../../docs/dev/cli.md](../../../docs/dev/cli.md).
- Do NOT leak rich markup into JSON mode.

## Dependencies
- Upstream: `yaya.core` (domain), `yaya.__version__`.
- External: `typer`, `rich`, `loguru`.
- See [../../../docs/dev/cli.md](../../../docs/dev/cli.md) for the full convention + extension checklist.
