# yaya

A Python AI agent built with engineering rigor.

[![CI](https://github.com/rararulab/yaya/actions/workflows/main.yml/badge.svg)](https://github.com/rararulab/yaya/actions/workflows/main.yml)
[![Release](https://img.shields.io/github/v/release/rararulab/yaya?sort=semver)](https://github.com/rararulab/yaya/releases/latest)

## Quick start

```bash
pip install https://github.com/rararulab/yaya/releases/latest/download/yaya-<VERSION>-py3-none-any.whl
yaya --help
```

Requires Python 3.14+. Other install options (prebuilt binary, from source) →
[docs/install.md](docs/install.md).

## Documentation

### For users

- [docs/install.md](docs/install.md) — binary, wheel, and source install.
- [docs/usage.md](docs/usage.md) — commands, JSON mode, examples.

### For contributors and agents

- [AGENT.md](AGENT.md) — entry index for any coding agent.
- [docs/architecture.md](docs/architecture.md) — layout and layering.
- [docs/workflow.md](docs/workflow.md) — issue → worktree → PR (MANDATORY).
- [docs/multi-agent.md](docs/multi-agent.md) — parallel agent development.
- [docs/cli.md](docs/cli.md) — CLI conventions and extension checklist.
- [docs/testing.md](docs/testing.md) — `just check` · `just test` · TDD.
- [docs/agent-spec.md](docs/agent-spec.md) — Oracle Agent Spec conformance.
- [docs/release.md](docs/release.md) — release-please automation.

### Org standards

Inherited from [`rararulab/.github`](https://github.com/rararulab/.github):
workflow, commit style, code comments, CLI design, anti-patterns, issue & PR
templates.

## License

MIT
