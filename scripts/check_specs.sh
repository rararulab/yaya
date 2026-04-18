#!/usr/bin/env bash
# Drive `agent-spec lifecycle` over every specs/*.spec, enforcing the
# two layers we can verify deterministically without an AI backend:
#   - lint     (parse + quality score; hard gate)
#   - boundary (declared Allowed/Forbidden paths vs actual diff; hard gate)
#
# Scenario-level verify SKIPs (no verifier covered this step) are
# expected today — they would need an AI backend per upstream's
# `--ai-mode` option and the `agent-spec-tool-first` skill. We count
# the SKIP total for visibility but do NOT fail the run on them.
#
# Locally without the binary: prints an install hint and exits 0 so a
# commit is not blocked. CI always has it, so CI enforces.
#
# See docs/dev/agent-spec.md for install and semantics.
set -euo pipefail

MIN_SCORE="${MIN_SCORE:-0.6}"
SPEC_DIR="${SPEC_DIR:-specs}"
CODE_DIR="${CODE_DIR:-.}"
CHANGE_SCOPE="${CHANGE_SCOPE:-worktree}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARSER="$SCRIPT_DIR/_parse_spec_result.py"
DETECTOR="$SCRIPT_DIR/_detect_owning_spec.py"

# Which spec (if any) does this PR own? Boundary violations on the
# owning spec hard-fail CI; everywhere else they stay soft-report.
# See scripts/_detect_owning_spec.py for the resolution chain.
OWNING_SPEC="$(python3 "$DETECTOR" 2>/dev/null || true)"
if [ -n "$OWNING_SPEC" ]; then
  echo "🎯 owning spec detected: $OWNING_SPEC"
else
  echo "ℹ️  no owning spec detected — boundary stays soft-report for all specs"
fi
echo

if ! command -v agent-spec >/dev/null 2>&1; then
  echo "⚠️  agent-spec not installed; skipping spec enforcement."
  echo "    Install: cargo install agent-spec --version 0.2.7 --locked"
  echo "    See docs/dev/agent-spec.md"
  exit 0
fi

shopt -s nullglob
specs=("$SPEC_DIR"/*.spec)
if [ ${#specs[@]} -eq 0 ]; then
  echo "ℹ️  no specs found under $SPEC_DIR/*.spec (skip)"
  exit 0
fi

# Assemble explicit --change flags from git diff. agent-spec 0.2.7's
# --change-scope worktree/staged produces broken paths on macOS
# (missing leading slash) so we feed relative paths directly.
change_args=()
if [ "$CHANGE_SCOPE" != "none" ] && git rev-parse --git-dir >/dev/null 2>&1; then
  case "$CHANGE_SCOPE" in
    worktree)
      mapfile -t files < <(
        { git diff --name-only HEAD; git ls-files --others --exclude-standard; } 2>/dev/null | sort -u
      )
      ;;
    staged)
      mapfile -t files < <(git diff --cached --name-only 2>/dev/null)
      ;;
    *)
      mapfile -t files < <(git diff --name-only HEAD 2>/dev/null)
      ;;
  esac
  for f in "${files[@]}"; do
    [ -z "$f" ] && continue
    change_args+=(--change "$f")
  done
fi

overall_exit=0
declare -a summary

for spec in "${specs[@]}"; do
  # lifecycle without --layers so the boundary pseudo-scenario appears
  # in verification.results; the parser filters on the "[boundaries]"
  # prefix to separate boundary fails from scenario skips.
  output="$(
    agent-spec lifecycle "$spec" \
      --code "$CODE_DIR" \
      --min-score "$MIN_SCORE" \
      "${change_args[@]}" \
      --format json 2>/dev/null || true
  )"

  if line="$(printf '%s' "$output" | python3 "$PARSER" --spec "$spec" --min-score "$MIN_SCORE" --owning-spec "$OWNING_SPEC")"; then
    echo "✅ $line"
    summary+=("✅ $line")
  else
    echo "❌ $line"
    summary+=("❌ $line")
    overall_exit=1
  fi
done

echo
echo "── agent-spec lifecycle summary ───────────────────"
printf '  %s\n' "${summary[@]}"
echo

if [ $overall_exit -ne 0 ]; then
  echo "❌ agent-spec enforcement failed"
  exit 1
fi

echo "✅ agent-spec enforcement green"
