#!/usr/bin/env bash
# Lint every specs/*.spec via agent-spec. Skip with a warning when the
# binary is absent locally (dev ergonomics); CI always has it, so CI
# enforces. Fails the run on any parse/format error.
#
# See docs/dev/agent-spec.md for install instructions.
set -euo pipefail

if ! command -v agent-spec >/dev/null 2>&1; then
  echo "⚠️  agent-spec not installed; skipping spec lint."
  echo "    Install: cargo install agent-spec --version 0.2.7 --locked"
  echo "    See docs/dev/agent-spec.md"
  exit 0
fi

shopt -s nullglob
specs=(specs/*.spec)

if [ ${#specs[@]} -eq 0 ]; then
  echo "ℹ️  no specs found under specs/*.spec (skip)"
  exit 0
fi

failed=0
for spec in "${specs[@]}"; do
  echo "🧪 lint $spec"
  if ! agent-spec lint "$spec"; then
    failed=1
  fi
done

if [ $failed -ne 0 ]; then
  echo "❌ one or more specs failed agent-spec lint"
  exit 1
fi

echo "✅ all specs passed agent-spec lint"
