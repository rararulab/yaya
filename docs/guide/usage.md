# Usage

```bash
yaya --help
yaya version
yaya hello --name world
```

## JSON mode

All commands accept `--json` and emit the canonical
`{"ok": bool, ...}` shape on stdout. See [CLI Conventions](../dev/cli.md) for the full
contract.

```bash
yaya hello --name world --json
# {"ok": true, "action": "hello", "greeting": "Hello, world!"}
```

## Dev-only commands

From a source checkout with [just](https://github.com/casey/just):

```bash
just run hello     # run the CLI via uv
just test          # pytest + coverage
just check         # ruff + mypy
just build         # wheel + sdist
just build-bin     # single-file binary via PyInstaller
```
