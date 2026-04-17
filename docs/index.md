# yaya

A **lightweight, kernel-style agent that grows itself.** A single
Python process (`yaya serve`) runs an event-driven kernel whose web UI
opens at `http://127.0.0.1:<port>`. Everything else is a plugin — and
yaya can author plugins on demand.

Read the product anchor: **[GOAL](goal.md)**.

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

- [Architecture](dev/architecture.md) — kernel + plugins layout.
- [Plugin Protocol](dev/plugin-protocol.md) — event catalog, ABI, categories (authoritative).
- [Workflow](dev/workflow.md) — issue → worktree → PR (MANDATORY).
- [Multi-Agent](dev/multi-agent.md) — parallel dispatch, hand-off rules.
- [CLI Conventions](dev/cli.md) — command pattern and extension checklist.
- [Web UI](dev/web-ui.md) — `yaya serve`, pi-web-ui build, WS event protocol.
- [Testing](dev/testing.md) — `just check` · `just test` · TDD.
- [Agent Spec (BDD Contracts)](dev/agent-spec.md) — `ZhangHanDong/agent-spec` workflow.
- [Code Comments & Docstrings](dev/code-comments.md) — Google Python Style Guide + yaya overlays.
- [Release](dev/release.md) — release-please automation.

## Org standards

Inherited from [`rararulab/.github`](https://github.com/rararulab/.github):
workflow, commit style, code comments, CLI design, anti-patterns, issue &
PR templates.

## License

MIT
