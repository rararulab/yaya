# Install

Requires Python 3.14+.

## Option 1: Prebuilt binary (recommended)

Grab the archive for your platform from the
[latest release](https://github.com/rararulab/yaya/releases/latest) and drop
the `yaya` binary somewhere on your `PATH`.

### Linux / macOS

```bash
# Linux x86_64
curl -L https://github.com/rararulab/yaya/releases/latest/download/yaya-$(curl -s https://api.github.com/repos/rararulab/yaya/releases/latest | grep tag_name | cut -d'"' -f4)-x86_64-unknown-linux-gnu.tar.gz \
  | tar -xz && sudo mv yaya /usr/local/bin/
yaya version
```

Other targets:

- `yaya-<VERSION>-aarch64-unknown-linux-gnu.tar.gz`  — Linux arm64
- `yaya-<VERSION>-aarch64-apple-darwin.tar.gz`       — macOS Apple Silicon

### Windows

Download `yaya-<VERSION>-x86_64-pc-windows-msvc.zip`, unzip, and add to `PATH`.

### Verify integrity

Every asset has a matching `.sha256` file:

```bash
sha256sum -c yaya-<VERSION>-<target>.tar.gz.sha256
```

## Option 2: Install from wheel

```bash
pip install https://github.com/rararulab/yaya/releases/latest/download/yaya-<VERSION>-py3-none-any.whl
```

## Option 3: From source

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```bash
git clone https://github.com/rararulab/yaya.git
cd yaya
just install
just run version
```
