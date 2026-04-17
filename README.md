# yaya

A Python agent.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```bash
just install     # create .venv, install deps, set up pre-commit
just check       # lint + type check
just test        # run pytest
just fmt         # format with ruff
just build       # build wheel + sdist
just run hello   # run the CLI
```

## Layout

```
src/yaya/        # package source
tests/           # pytest suite
justfile         # task runner
pyproject.toml   # project + tool config
```
