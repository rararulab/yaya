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
- `serve.py` — boot kernel; load bundled `web` adapter plugin; open browser.
- `version.py` — prints kernel + loaded-plugin versions (also `--version`).
- `update.py` — self-update the yaya binary/wheel; delegates to `yaya.core.updater`.
- `hello.py` — smoke-test: boot kernel, emit a synthetic event round-trip, print OK.
- `plugin.py` — `plugin list / install / remove`; delegates to `yaya.kernel.registry`.

Adding a new built-in command requires a GOAL.md amendment. All other
features (adapters, tools, skills, memory, LLM providers, strategies)
ship as plugins.

Rules:
- Module exports exactly `register(app)` at top-level side-effect scope — nothing else runs at import.
- Body pattern: (1) parse args, (2) call into `yaya.kernel.*` or `yaya.core.*`, (3) render with `emit_ok`/`emit_error`, (4) non-zero exit on failure.
- Destructive commands require `--dry-run` and `--yes`.

## Interaction (patterns)
- Do NOT put business logic here — it belongs in `yaya.kernel.*` or `yaya.core.*`.
- Do NOT import from `yaya.plugins.*` — commands drive the kernel, not plugins directly.
- Do NOT call `print` / `typer.echo` / `Console().print`.
- Register the new module in `../__init__.py` — otherwise it won't be discoverable.
- Error paths must emit a `suggestion` field in JSON mode.

## Budget & Loading
- Full "adding a command" checklist: [../../../../docs/dev/cli.md](../../../../docs/dev/cli.md).
- Output contract: [../output.py](../output.py) + [../AGENT.md](../AGENT.md).
