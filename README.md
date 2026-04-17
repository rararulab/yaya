# yaya

A Python agent.

[![CI](https://github.com/rararulab/yaya/actions/workflows/main.yml/badge.svg)](https://github.com/rararulab/yaya/actions/workflows/main.yml)
[![Release](https://img.shields.io/github/v/release/rararulab/yaya?sort=semver)](https://github.com/rararulab/yaya/releases/latest)

## Install

### Option 1: Prebuilt binary (recommended)

Grab the archive for your platform from the [latest release](https://github.com/rararulab/yaya/releases/latest) and drop the `yaya` binary somewhere on your `PATH`.

**Linux / macOS**

```bash
# Linux x86_64
curl -L https://github.com/rararulab/yaya/releases/latest/download/yaya-$(curl -s https://api.github.com/repos/rararulab/yaya/releases/latest | grep tag_name | cut -d'"' -f4)-x86_64-unknown-linux-gnu.tar.gz \
  | tar -xz && sudo mv yaya /usr/local/bin/

# Or pick manually:
# - yaya-<VERSION>-x86_64-unknown-linux-gnu.tar.gz   (Linux x86_64)
# - yaya-<VERSION>-aarch64-unknown-linux-gnu.tar.gz  (Linux arm64)
# - yaya-<VERSION>-aarch64-apple-darwin.tar.gz       (macOS Apple Silicon)

yaya version
```

**Windows**

Download `yaya-<VERSION>-x86_64-pc-windows-msvc.zip`, unzip, and add to `PATH`.

Every asset has a matching `.sha256` file to verify integrity:

```bash
sha256sum -c yaya-<VERSION>-<target>.tar.gz.sha256
```

### Option 2: Install from wheel

```bash
pip install https://github.com/rararulab/yaya/releases/latest/download/yaya-<VERSION>-py3-none-any.whl
```

Requires Python 3.14+.

### Option 3: From source

```bash
git clone https://github.com/rararulab/yaya.git
cd yaya
uv sync
uv run yaya version
```

Requires [uv](https://docs.astral.sh/uv/).

## Usage

```bash
yaya --help
yaya hello --name world
yaya version
```

## Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```bash
just install     # create .venv, install deps, set up pre-commit
just check       # ruff lint + format check + mypy
just fmt         # auto-format with ruff
just test        # run pytest with coverage
just build       # build wheel + sdist
just build-bin   # build standalone binary with PyInstaller
just run hello   # run the CLI
```

## Release flow

Releases are fully automated via [release-please](https://github.com/googleapis/release-please). A single workflow (`release-please.yml`) owns the end-to-end flow:

1. Land commits on `main` using [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `feat!:` for breaking, etc.).
2. release-please opens/updates a "chore(main): release X.Y.Z" PR that bumps `pyproject.toml`, updates `CHANGELOG.md`, and advances `.release-please-manifest.json`.
3. When that PR is merged, release-please creates the git tag and the GitHub Release in the same workflow run.
4. Downstream jobs in the same run build PyInstaller binaries for 4 targets, build the wheel + sdist, generate SHA256 sums, and upload everything as assets on the release.

No manual tagging or separate tag-triggered workflow is needed.

## Layout

```
src/yaya/        # package source
tests/           # pytest suite
scripts/         # release helpers
justfile         # task runner
pyproject.toml   # project + tool config
CHANGELOG.md     # auto-maintained by release-please
```

## License

MIT
