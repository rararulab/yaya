# agent-spec — BDD Contracts for Agent Work

Canonical tool: [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec).
An AI-native BDD / spec verification framework. Humans author a
**task contract**; the agent implements against it; `agent-spec` lints
the contract and — with an AI backend — verifies compliance.

**In yaya, every non-trivial feature PR is backed by a `.spec` contract.**
Trivial means: single-line typos, doc-only changes, dependency bumps.
Everything else — new commands, new `core/` or `kernel/` modules, new
plugins, behavior changes — must have a spec.

## What this harness enforces today

`scripts/check_specs.sh` runs `agent-spec lifecycle` on every
`specs/*.spec` and classifies findings into hard-fail vs soft-report,
matching the upstream [`contract-guard.yml`](https://github.com/ZhangHanDong/agent-spec/blob/main/.github/workflows/contract-guard.yml)
model (which uses `continue-on-error: true` for the same reasons).
The wrapper then runs in three places: `just check`, the pre-commit
hook for staged `specs/*.spec` changes, and the CI `check` job.
`scripts/check_feature_sync.py` runs alongside it in `just check`, CI,
and pre-commit for staged `.spec` / `.feature` changes so executable
Gherkin cannot drift from the task contract.

### Why `lifecycle` and not raw `agent-spec guard`?

Upstream ships two CI-oriented entry points. `agent-spec guard` lints
every spec and runs the full verify layer against a git change scope
in one shot. It is attractive on paper but currently unusable as a
merge gate for this repo: the `verify` layer needs an AI backend, and
without one it emits `skip` verdicts on every scenario — `guard` exits
non-zero on those skips, so a raw `guard` invocation would block
every PR even when nothing is wrong.

`agent-spec lifecycle` exposes the same pipeline (lint → boundary →
verify → report) per spec and returns structured JSON we can
classify. `scripts/check_specs.sh` + `scripts/_parse_spec_result.py`
implement the guard semantics the harness promises (lint and quality
hard-gated, owning-spec boundary hard-gated, non-owning boundary and
verify skips soft-reported) on top of that JSON. When an AI backend
lands and `verify` starts emitting real verdicts, we will switch the
wrapper to raw `guard` and drop the parser. Until then, the wrapper
IS the guard for this repo. Regression coverage for the decision
logic lives in `tests/scripts/test_check_spec_result.py`.

**Hard-fail (blocks merge):**

- Parse error — bad frontmatter, unresolved `inherits:`, malformed
  scenario block.
- `quality_score < 0.6` — sloppy spec authoring; see the lint rules
  below.
- Boundary violation on **the owning spec** — see "PR ownership"
  below. Each PR can own at most one spec; that spec's Allowed /
  Forbidden lists are enforced as a hard gate.
- Non-boundary scenario failures — reserved for when an AI backend
  lands and the `verify` layer returns real `fail` verdicts.

**Soft-report (visible in logs, does not block merge):**

- Boundary violations on **non-owning specs**. A repo with N specs
  evaluates boundary per spec; a cross-cutting PR will naturally
  violate every spec except the one it owns. Reported so reviewers
  see which surfaces a PR touched, but not failed.
- Scenario verify SKIPs — `no verifier covered this step`. The
  `verify` layer needs an AI backend (`--ai-mode` with a real LLM)
  or the `agent-spec-tool-first` skill to interpret Given/When/Then
  against code. Not wired today; tracked separately.

## PR ownership (`scripts/_detect_owning_spec.py`)

Which spec does a PR own? Resolution chain (first match wins):

1. **Branch name convention** — `issue-{N}-{slug}` (or
   `feat/slug`, `fix/slug`, …). The slug is matched against
   `specs/*.spec` file stems by prefix. Exactly one candidate must
   match; ambiguous slugs return no owner with a warning. Works in
   CI (`$GITHUB_HEAD_REF`) and in local worktrees.
2. **PR body trailer** — a line `Spec: specs/<path>.spec` anywhere
   in the PR body. Fetched via `gh pr view --json body`. Used when
   the branch name does not resolve.
3. **HEAD commit trailer** — a line `Spec: specs/<path>.spec` in
   the last commit message. Last-resort fallback for stacked
   branches or offline cases.
4. **No match** — meta PRs (infra, docs, dep bumps) have no owner.
   Boundary stays soft-reported for every spec; the build is not
   blocked on boundary for these PRs.

The pull request template has an optional `Spec:` line so authors
can override when the branch name heuristic is wrong.

Spec files MUST live under `specs/<slug>.spec` (no `.md` suffix; the
tool parses YAML frontmatter, not Markdown).

## Lint rules the wrapper exposes

Invoked via the `lint` layer of `agent-spec lifecycle`; visible in the
summary line as `lint_issues=N quality=X.XX`:

- `[implicit-dep]` — parameter referenced without a `Given` step that
  establishes it.
- `[decision-coverage]` — `## Decisions` entry with no matching scenario.
- `[error-path]` — spec has no error-path scenarios.
- `[vague-verb]` — constraint uses a hand-wavy verb (`manage`, `handle`).
- `[platform-decision-tag]` — decision references a platform-specific
  tool (`pip`, `cargo`, …) without a `[platform-specific]` tag.

A quality score is aggregated across determinism / testability /
coverage; `min_score=0.6` is the floor.

## Install

```bash
cargo install agent-spec --version 0.2.7 --locked
agent-spec --version
```

The version is pinned in `.github/workflows/main.yml` and in
`scripts/check_specs.sh`. Upgrade both together.

Optional: install the corresponding agent skill for your runtime
(`agent-spec-tool-first` for Claude Code, equivalents for Codex /
Cursor). Not required for CI.

If you do not want to install Rust locally, skip it — the wrapper
script prints a friendly notice and succeeds locally, and CI enforces
the check on every PR.

### Missing-binary behavior (decision tree)

`scripts/check_specs.sh` resolves the "binary missing" case in this
order:

1. **Under `$CI` or `$GITHUB_ACTIONS`** — hard-fail (exit 1). A CI
   run without `agent-spec` is always a CI config bug (cargo install
   step dropped, cache-key drift, etc.); silently exiting 0 would let
   spec drift land without enforcement. Fix the workflow, do not
   route around it.
2. **`SKIP_AGENT_SPEC=1`** — soft-skip (exit 0) with an info message.
   Explicit opt-out for contributors who deliberately want to bypass
   (e.g., doc-only branches, sandbox experiments). Preferred over
   relying on the binary being absent.
3. **Local, no opt-out, binary missing** — soft-skip (exit 0) with a
   warning. Keeps the Rust-free contributor experience intact. This
   default is scheduled to flip to hard-fail in a future release once
   the toolchain is universally installed in the team dev shell.

## Task contract shape (`specs/<slug>.spec`)

```
spec: task
name: "<slug>"
tags: [<optional tags>]
---

## Intent

One stakeholder-readable paragraph — what and why.

## Decisions

- Fixed technical choices that are NOT up for debate in this PR.

## Boundaries

### Allowed Changes
- `src/yaya/cli/commands/<new>.py`
- `tests/cli/test_<new>.py`

### Forbidden
- `src/yaya/core/updater.py`
- `pyproject.toml` dependencies section

## Completion Criteria

Scenario: happy path
  Test:
    Package: yaya
    Filter: tests/cli/test_<new>.py::test_happy
  Level: unit
  Given <precondition>
  When <action>
  Then <observable outcome>

Scenario: error case
  Test:
    Package: yaya
    Filter: tests/cli/test_<new>.py::test_error
  Level: unit
  Given <precondition>
  When <invalid input>
  Then <exit non-zero with JSON error + suggestion>

## Out of Scope

- Things deliberately deferred.
```

Minimum 3 scenarios: happy path + error + edge case. Every scenario
MUST have a `Test:` block binding it to a concrete test function.

## Developer workflow

```bash
# 1. Author contract (or accept one written by a design agent)
$EDITOR specs/<slug>.spec

# 2. Iterate; re-lint until clean
agent-spec lint specs/<slug>.spec

# 3. Implement inside the issue worktree

# 4. Verify locally (full lifecycle plus executable BDD mirror)
just check-specs
just check-features

# 5. Before PR: ensure `just check` is green (it runs check-specs too)
just check && just test
```

Pre-commit runs `scripts/check_specs.sh` automatically on any staged
`.spec` file, and `scripts/check_feature_sync.py` on staged `.spec` or
`.feature` files.

## Contract authoring rules

- **Intent is stakeholder-readable.** No code, no jargon the user didn't use.
- **Decisions are _decisions_, not suggestions.** If it's still open,
  it does not belong here — brainstorm elsewhere.
- **Boundaries are narrow.** List explicit allowed paths. A spec that
  "allows everything" is useless once boundary enforcement is on.
- **Scenarios are observable.** Assert on exit code, stdout JSON shape,
  file-on-disk state — never on "the function was called".
- **One contract, one issue.** Split large work into stacked
  contracts; see [workflow.md](workflow.md).

## Relationship to the yaya BDD issue template

`.github/ISSUE_TEMPLATE/bdd_task.yml` captures the same sections
(Description / Plan Spec / agent-spec contract draft / Design spec).
Use the issue template to open the issue, then commit the executable
contract as `specs/<slug>.spec`.

## What NOT To Do

- Do NOT open a feature PR without a `.spec` (trivia excepted).
- Do NOT change scope mid-PR without updating the contract in the
  same commit.
- Do NOT write scenarios without a `Test:` selector — lint flags them.
- Do NOT relax `Boundaries` to make the build pass — revisit scope
  instead.
- Do NOT use the old `.spec.md` extension. The tool parses
  `.spec` files with YAML frontmatter.
