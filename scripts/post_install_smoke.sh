#!/usr/bin/env bash
# scripts/post_install_smoke.sh — reproduce the CI post-install smoke locally.
#
# Builds wheel + sdist, creates two fresh venvs, installs each into its own
# venv, and runs `pytest tests/e2e -v` against the installed artifact.
# Devs can use this to debug wheel/sdist parity without pushing to CI.
#
# Usage:
#   scripts/post_install_smoke.sh            # wheel + sdist
#   scripts/post_install_smoke.sh wheel      # wheel only
#   scripts/post_install_smoke.sh sdist      # sdist only
#
# Environment:
#   PYTHON     — Python interpreter to use for the venv (default: python3.14)
#   DIST_DIR   — where build outputs and venvs live (default: ./dist)
#
# Non-goals:
#   - Matrix across multiple Python versions (CI owns that).
#   - PyInstaller binary smoke — run `just build-bin` then
#     `YAYA_BIN=$(pwd)/dist/yaya pytest tests/e2e -v`.
set -euo pipefail

PYTHON="${PYTHON:-python3.14}"
DIST_DIR="${DIST_DIR:-dist}"
if [ "$#" -eq 0 ]; then
  TARGETS=(wheel sdist)
else
  TARGETS=("$@")
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "❌ $PYTHON not on PATH — set PYTHON=<interpreter> and retry." >&2
  exit 1
fi

if ! command -v uvx >/dev/null 2>&1; then
  echo "❌ uvx not on PATH — install uv: https://docs.astral.sh/uv/" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "📦 Building wheel + sdist into $DIST_DIR/"
rm -rf "$DIST_DIR"
uvx --from build pyproject-build --installer uv --outdir "$DIST_DIR"

WHEEL="$(ls -1t "$DIST_DIR"/*.whl 2>/dev/null | head -1 || true)"
SDIST="$(ls -1t "$DIST_DIR"/*.tar.gz 2>/dev/null | head -1 || true)"

if [ -z "$WHEEL" ] || [ -z "$SDIST" ]; then
  echo "❌ missing build output: wheel=$WHEEL sdist=$SDIST" >&2
  exit 1
fi

smoke_one() {
  local name="$1"
  local artifact="$2"
  local venv=".smoke-${name}-venv"

  echo
  echo "── $name smoke ($(basename "$artifact")) ─────────────────────────"
  rm -rf "$venv"
  "$PYTHON" -m venv "$venv"
  # shellcheck disable=SC1091
  . "$venv/bin/activate"
  python -m pip install --upgrade pip >/dev/null
  # Match CI's `pip install dist/*.whl pytest pytest-timeout pytest-bdd pytest-asyncio websockets`.
  python -m pip install "$artifact" pytest pytest-timeout pytest-bdd pytest-asyncio websockets >/dev/null
  echo "🐍 yaya installed from $name: $(yaya --version 2>/dev/null || echo '?')"
  python -m pytest tests/e2e -v
  deactivate
}

for target in "${TARGETS[@]}"; do
  case "$target" in
    wheel) smoke_one wheel "$WHEEL" ;;
    sdist) smoke_one sdist "$SDIST" ;;
    *) echo "❌ unknown target: $target (expected wheel|sdist)" >&2; exit 1 ;;
  esac
done

echo
echo "✅ post-install smoke green for: ${TARGETS[*]}"
