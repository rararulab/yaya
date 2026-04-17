# CLI Conventions

yaya's default experience is the web UI (`yaya serve`). The CLI stays
minimal and scripting-friendly. Canonical org spec:
`rararulab/.github/docs/agent-friendly-cli.md` — yaya follows it strictly.
This file captures yaya-specific mechanics.

## Command surface (1.0)

| Command | Purpose |
|---------|---------|
| `yaya serve` | **Default.** Start the single-process web server; open browser. |
| `yaya version` | Print version. |
| `yaya plugin list` | List installed plugins. |
| `yaya plugin install <src>` | Install from path / URL / registry. |
| `yaya plugin remove <name>` | Uninstall. |

Everything else — `hello`, `update`, future integrations — ships as a
plugin. Adding a built-in subcommand requires a GOAL.md amendment.

## Pattern

Every CLI subcommand:

1. Builds a result in `core/` or `kernel/` (pure logic, no printing, no `sys.exit`).
2. Calls `emit_ok` / `emit_error` from `yaya.cli.output` — never `print`,
   `typer.echo`, or `Console().print` directly.
3. Exits non-zero on failure; `fatal()` is the one-liner helper.

## `yaya serve` specifics

```bash
yaya serve                       # bind 127.0.0.1:<auto>, open browser
yaya serve --port 7456            # fixed port
yaya serve --no-open              # do not launch browser
yaya serve --dev                  # proxy to `vite dev` for UI HMR
yaya serve --json                 # JSON lifecycle events on stdout (addr, pid, shutdown)
```

- Bind is always `127.0.0.1` through 1.0 (see [GOAL.md](../goal.md)).
  There is **no** `--host` flag.
- Exit cleanly on `SIGINT` / `SIGTERM`; emit a final
  `{"ok": true, "action": "shutdown", ...}` under `--json`.
- Kernel startup errors exit non-zero with a `suggestion` field.

## JSON mode (`--json`)

- Stdout carries `{"ok": bool, ...}`. Contract enforced by `cli/output.py`.
- Success: `{"ok": true, "action": "<verb>", ...data}`.
- Error: `{"ok": false, "error": "...", "suggestion": "...", ...data}`.
- Human logs / warnings go to **stderr** (`warn(...)`).

## Extending the CLI — checklist

Adding a new CLI command is a GOAL.md amendment (see surface above). If
approved, follow this:

- [ ] Amend GOAL.md's "Command surface" in the same PR.
- [ ] Create `src/yaya/cli/commands/<name>.py` exposing `register(app)`.
- [ ] Register it in `src/yaya/cli/__init__.py`.
- [ ] Body pattern: build a structured result in `core/` or `kernel/`,
      then `emit_ok` / `emit_error`. No direct printing.
- [ ] Add `epilog=EXAMPLES` with at least one runnable example.
- [ ] Destructive? Add `--dry-run` and `--yes`.
- [ ] Error paths must emit a `suggestion` and exit non-zero.
- [ ] Add `tests/cli/test_<name>.py` covering text mode, `--json`, and
      at least one failure path (exit code + JSON shape).
- [ ] Network/disk work: add `tests/core/test_<module>.py` with
      `pytest-httpx` mocks — no real network in tests.
