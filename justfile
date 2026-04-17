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
    @echo "🧠 Type-checking with mypy"
    uv run mypy

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

# Run tests
test:
    @echo "🧪 Running pytest"
    uv run python -m pytest --cov --cov-report=term-missing

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
