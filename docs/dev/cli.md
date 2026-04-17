# CLI Conventions

yaya's default experience is the web adapter (`yaya serve`). The CLI is
minimal — it exists only to bootstrap the kernel and manage plugins.
Canonical org spec: `rararulab/.github/docs/agent-friendly-cli.md`.
This file captures yaya-specific mechanics.

## Command surface (1.0, kernel built-ins only)

| Command | Purpose |
|---------|---------|
| `yaya serve` | **Default.** Boot kernel; load bundled `web` adapter plugin; open browser. |
| `yaya version` | Print kernel + loaded-plugin versions. |
| `yaya update` | Self-update the yaya binary/wheel. |
| `yaya hello` | Smoke-test: boot kernel, emit a synthetic event round-trip, print OK. |
| `yaya plugin list` | List installed plugins with category and status. |
| `yaya plugin install <src>` | Install a plugin (pip package / path / registry URL). |
| `yaya plugin remove <name>` | Uninstall. |

Everything else — adapters (other than `web`), tools, skills, memory,
LLM providers, strategies — is a plugin.

**Adding a new built-in CLI subcommand requires a GOAL.md amendment.**
The built-ins above are the self-bootstrap surface; new capability
belongs in plugins.

## Pattern

Every CLI subcommand:

1. Builds a result by calling into `kernel/` (for `serve`, `hello`,
   `plugin *`) or `core/` (for `version`, `update`). Pure logic, no
   printing, no `sys.exit`.
2. Calls `emit_ok` / `emit_error` from `yaya.cli.output` — never
   `print`, `typer.echo`, or `Console().print` directly.
3. Exits non-zero on failure; `fatal()` is the one-liner helper.

## `yaya serve` specifics

```bash
yaya serve                       # bind 127.0.0.1:<auto>, open browser
yaya serve --port 7456           # fixed port
yaya serve --no-open             # do not launch browser
yaya serve --dev                 # proxy to `vite dev` for UI HMR
yaya serve --strategy <id>       # pick the active strategy plugin (default: react)
yaya serve --json                # JSON lifecycle events on stdout (addr, pid, shutdown)
```

- Bind is always `127.0.0.1` through 1.0 (see [GOAL.md](../goal.md)).
  There is **no** `--host` flag.
- Exit cleanly on `SIGINT` / `SIGTERM`; emit a final
  `{"ok": true, "action": "shutdown", ...}` under `--json`.
- Kernel startup errors exit non-zero with a `suggestion` field.
- Plugin load failures surface as `plugin.error` events but do NOT
  fail the serve command — the kernel keeps running (see
  [plugin-protocol.md](plugin-protocol.md#plugin-failure-model)).

## `yaya plugin` specifics

```bash
yaya plugin list                       # all discovered plugins, category, status
yaya plugin install yaya-tool-bash     # pip resolve from PyPI
yaya plugin install ./my-plugin        # editable install from path
yaya plugin install --json <src>       # machine-readable output
yaya plugin remove yaya-tool-bash
```

- `install` shells to `pip install` under the hood and refreshes the
  registry without requiring a `yaya serve` restart for third-party
  plugins (implementation detail — see
  [plugin-protocol.md](plugin-protocol.md#plugin-discovery-and-loading)).
- Removing a bundled plugin is an error — the CLI rejects it with a
  `suggestion` pointing at `yaya plugin disable` (reserved for 0.5+).

## JSON mode (`--json`)

- Stdout carries `{"ok": bool, ...}`. Contract enforced by
  `cli/output.py`.
- Success: `{"ok": true, "action": "<verb>", ...data}`.
- Error: `{"ok": false, "error": "...", "suggestion": "...", ...data}`.
- Human logs / warnings go to **stderr** (`warn(...)`).

## Extending the CLI — checklist (rarely used)

Adding a new built-in CLI command is a **GOAL.md amendment**. If the
amendment is approved:

- [ ] Amend GOAL.md's "Command surface" in the same PR.
- [ ] Create `src/yaya/cli/commands/<name>.py` exposing `register(app)`.
- [ ] Register it in `src/yaya/cli/__init__.py`.
- [ ] Body pattern: build a structured result in `kernel/` or
      `core/`, then `emit_ok` / `emit_error`. No direct printing.
- [ ] Add `epilog=EXAMPLES` with at least one runnable example.
- [ ] Destructive? Add `--dry-run` and `--yes`.
- [ ] Error paths must emit a `suggestion` and exit non-zero.
- [ ] Add `tests/cli/test_<name>.py` covering text mode, `--json`,
      and at least one failure path (exit code + JSON shape).
- [ ] Network/disk work: `pytest-httpx` mocks — no real network in
      tests.
