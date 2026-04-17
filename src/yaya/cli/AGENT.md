# src/yaya/cli — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Thin shell. Parses args, delegates to `core/`, renders through `output.py`.

## External Reality
- `tests/cli/` is ground truth for every subcommand: text mode, `--json`, and a failure path.
- JSON contract enforced by `output.py`: `{"ok": bool, ...}`. Schema regressions break agent consumers.
- `--help` output and exit codes are part of the public surface — tests assert them.

## Constraints
- `__init__.py` — builds the Typer `app`, defines `CLIState`, wires the root callback (`--json`, `-v`, `-q`, `--version`). Each command module calls `register(app)` here.
- `output.py` — canonical renderers: `emit_ok`, `emit_error`, `warn`, `fatal`. Never edit the JSON shape without a CHANGELOG breaking-change entry.
- `commands/` — one file per subcommand. See [commands/AGENT.md](commands/AGENT.md).
- `CLIState` attached to `ctx.obj` in the root callback; commands read it via `ctx.obj`, never module globals.
- Errors exit non-zero (`raise typer.Exit(code=N)` or `fatal(...)`).

## Interaction (patterns)
- **No business logic here.** CLI parses args → calls `core/` → renders.
- Commands call `emit_ok` / `emit_error` — never `print`, `typer.echo`, `Console().print` (except `__init__.py` for `--help`/`--version` short-circuits).
- Defer heavy `yaya.core.*` imports into function bodies where possible to keep `--help` fast.
- Do NOT leak rich markup into JSON mode.
- Adding a command ⇒ update [commands/AGENT.md](commands/AGENT.md) and [../../../docs/dev/cli.md](../../../docs/dev/cli.md) in the same PR.

## Budget & Loading
- Full CLI convention + extension checklist: [../../../docs/dev/cli.md](../../../docs/dev/cli.md).
- Org CLI spec: [../../../docs/dev/agent-friendly-cli.md](../../../docs/dev/agent-friendly-cli.md).
