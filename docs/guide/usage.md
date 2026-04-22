# Usage

```bash
yaya --help
yaya version
yaya doctor
```

## JSON mode

All commands accept `--json` and emit the canonical
`{"ok": bool, ...}` shape on stdout. See [CLI Conventions](../dev/cli.md) for the full
contract.

```bash
yaya --json doctor
# {"ok": true, "action": "doctor", "roundtrip": {...}, "plugins": [...]}
```

## Dev-only commands

From a source checkout with [just](https://github.com/casey/just):

```bash
just run doctor    # run the CLI via uv
just test          # pytest + coverage
just check         # ruff + mypy
just build         # wheel + sdist
just build-bin     # single-file binary via PyInstaller
```
