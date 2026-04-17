# src/yaya/cli/commands — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
One file per subcommand. Each exports `register(app: typer.Typer) -> None`.

## External Reality
- Every command has a sibling test at `tests/cli/test_<name>.py` covering text mode, `--json`, and at least one failure path (exit code + JSON shape).
- `--help` epilog must contain at least one runnable example — tested by snapshot.
- CI rejects a registered command with no test.

## Constraints
Current commands (1.0 surface — see [GOAL.md](../../../../GOAL.md)):
- `serve.py` — default entrypoint; boots kernel + FastAPI + web UI in one process.
- `version.py` — prints `__version__` (also available via `--version`).
- `plugin.py` — `plugin list / install / remove`; delegates to `yaya.kernel.plugins`.

Adding a new built-in command requires a GOAL.md amendment. All other
features ship as plugins.

Rules:
- Module exports exactly `register(app)` at top-level side-effect scope — nothing else runs at import.
- Body pattern: (1) parse args, (2) call `yaya.core.*` for logic, (3) render with `emit_ok`/`emit_error`, (4) non-zero exit on failure.
- Destructive commands require `--dry-run` and `--yes`.

## Interaction (patterns)
- Do NOT put business logic here — it belongs in `yaya.core.*`.
- Do NOT call `print` / `typer.echo` / `Console().print`.
- Register the new module in `../__init__.py` — otherwise it won't be discoverable.
- Error paths must emit a `suggestion` field in JSON mode.

## Budget & Loading
- Full "adding a command" checklist: [../../../../docs/dev/cli.md](../../../../docs/dev/cli.md).
- Output contract: [../output.py](../output.py) + [../AGENT.md](../AGENT.md).
