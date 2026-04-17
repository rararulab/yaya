# yaya

A Python AI agent built with engineering rigor.

## Quick start

```bash
pip install https://github.com/rararulab/yaya/releases/latest/download/yaya-<VERSION>-py3-none-any.whl
yaya --help
```

Requires Python 3.14+. Other install options → [Install](guide/install.md).

## User Guide

- [Install](guide/install.md) — binary, wheel, or source.
- [Usage](guide/usage.md) — commands, `--json` contract, examples.

## Development

- [Architecture](dev/architecture.md) — layout and layering.
- [Workflow](dev/workflow.md) — issue → worktree → PR (MANDATORY).
- [Multi-Agent](dev/multi-agent.md) — parallel dispatch, hand-off rules.
- [CLI Conventions](dev/cli.md) — command pattern and extension checklist.
- [Testing](dev/testing.md) — `just check` · `just test` · TDD.
- [Agent Spec Conformance](dev/agent-spec.md) — Oracle Agent Spec rules.
- [Release](dev/release.md) — release-please automation.

## Org standards

Inherited from [`rararulab/.github`](https://github.com/rararulab/.github):
workflow, commit style, code comments, CLI design, anti-patterns, issue &
PR templates.

## License

MIT
