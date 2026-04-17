# yaya

A **lightweight, kernel-style agent that grows itself.** A single
Python process (`yaya serve`) runs an event-driven kernel whose web UI
opens in your browser at `http://127.0.0.1:<port>`. Plugins extend
everything else — and yaya can write its own plugins on demand.

Read the full anchor: **[GOAL.md](GOAL.md)**.

[![CI](https://github.com/rararulab/yaya/actions/workflows/main.yml/badge.svg)](https://github.com/rararulab/yaya/actions/workflows/main.yml)
[![Release](https://img.shields.io/github/v/release/rararulab/yaya?sort=semver)](https://github.com/rararulab/yaya/releases/latest)
[![Docs](https://img.shields.io/badge/docs-rararulab.github.io%2Fyaya-blue)](https://rararulab.github.io/yaya/)

## Quick start

```bash
pip install https://github.com/rararulab/yaya/releases/latest/download/yaya-<VERSION>-py3-none-any.whl
yaya --help
```

Requires Python 3.14+. Other install options →
[docs/guide/install.md](docs/guide/install.md).

## Documentation

Full site: **<https://rararulab.github.io/yaya/>**

### User Guide

- [docs/guide/install.md](docs/guide/install.md)
- [docs/guide/usage.md](docs/guide/usage.md)

### Development (for contributors and coding agents)

- [AGENT.md](AGENT.md) — agent entry index.
- [docs/dev/architecture.md](docs/dev/architecture.md)
- [docs/dev/workflow.md](docs/dev/workflow.md) — issue → worktree → PR.
- [docs/dev/multi-agent.md](docs/dev/multi-agent.md)
- [docs/dev/cli.md](docs/dev/cli.md)
- [docs/dev/web-ui.md](docs/dev/web-ui.md)
- [docs/dev/testing.md](docs/dev/testing.md)
- [docs/dev/agent-spec.md](docs/dev/agent-spec.md)
- [docs/dev/release.md](docs/dev/release.md)

### Org standards

Inherited from [`rararulab/.github`](https://github.com/rararulab/.github).

## License

MIT
