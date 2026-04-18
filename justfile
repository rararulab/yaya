# Default: list recipes
default:
    @just --list

# Install virtualenv + pre-commit hooks
install:
    @echo "🚀 Creating virtual environment using uv"
    uv sync
    uv run pre-commit install

# Lint + type check Python code
check:
    @echo "🧹 Linting with ruff"
    uv run ruff check .
    @echo "🎨 Checking format with ruff"
    uv run ruff format --check .
    @echo "🧠 Type-checking with mypy (strict)"
    uv run mypy --strict
    @echo "🧠 Type-checking with pyright (pylance parity)"
    uv run pyright
    @echo "🧾 Linting specs with agent-spec"
    @bash scripts/check_specs.sh
    @echo "🎭 Checking .feature / .spec sync"
    @uv run python scripts/check_feature_sync.py

# Lint only the BDD specs (skipped if agent-spec is not installed)
check-specs:
    @bash scripts/check_specs.sh

# Verify .feature files stay in sync with their matching .spec scenarios
check-features:
    @uv run python scripts/check_feature_sync.py

# Run all pre-commit hooks + lock file consistency (CI parity)
check-all: check
    @echo "🔒 Checking lock file consistency"
    uv lock --locked
    @echo "🪝 Running pre-commit hooks"
    uv run pre-commit run -a

# Format code
fmt:
    @echo "✨ Formatting with ruff"
    uv run ruff format .
    uv run ruff check --fix .

# Run unit + CLI tests (default suite)
test:
    @echo "🧪 Running pytest"
    uv run python -m pytest --cov --cov-report=term-missing

# Build wheel, install into a fresh venv, run post-install smoke
test-e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "📦 Building wheel"
    uvx --from build pyproject-build --installer uv --outdir dist --wheel
    wheel="$(ls -t dist/*.whl | head -1)"
    echo "🚀 Installing $wheel into a fresh venv and running smoke"
    rm -rf .smoke-venv
    python3.14 -m venv .smoke-venv
    . .smoke-venv/bin/activate
    python -m pip install --upgrade pip >/dev/null
    python -m pip install "$wheel" pytest pytest-timeout >/dev/null
    python -m pytest tests/e2e -v

# Remove build artifacts
clean:
    @echo "🧽 Cleaning build artifacts"
    rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml

# Build wheel + sdist
build: clean
    @echo "📦 Building distribution"
    uvx --from build pyproject-build --installer uv

# Build a single-file binary via PyInstaller (output: dist/yaya)
build-bin:
    @echo "🛠️  Building standalone binary (PyInstaller)"
    uv run pyinstaller yaya.spec --clean --noconfirm

# Show current project version
version:
    @uv run python -c "from yaya import __version__; print(__version__)"

# Run the CLI
run *ARGS:
    uv run yaya {{ARGS}}

# Serve documentation locally with hot reload
docs:
    @echo "📖 Serving docs at http://127.0.0.1:8000"
    uv run --group docs mkdocs serve

# Build documentation and fail on warnings (CI parity)
docs-test:
    @echo "📖 Building docs (strict)"
    uv run --group docs mkdocs build -s

# --- Web UI (src/yaya/web/) — pi-web-ui consumer ------------------------------
# Install npm deps (run once after clone / lockfile change)
web-install:
    @echo "📦 npm ci in src/yaya/web"
    cd src/yaya/web && npm ci

# Build static assets into src/yaya/web/static/ (CI + pre-wheel)
web-build:
    @echo "🌐 Building web UI (pi-web-ui)"
    cd src/yaya/web && npm run build

# Dev server with HMR (pair with `yaya serve --dev` in another shell)
web-dev:
    @echo "🌐 vite dev server (HMR)"
    cd src/yaya/web && npm run dev

# Lint + type check TS (biome + tsc --noEmit)
web-check:
    @echo "🌐 Web UI check"
    cd src/yaya/web && npm run check
