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

- **Lint** on every PR and pre-commit: parse errors, missing
  frontmatter, quality metrics (determinism / testability / coverage),
  unbound scenarios, vague verbs.
- Spec files MUST live under `specs/<slug>.spec` (no `.md` suffix; the
  tool parses a YAML-front-matter format, not Markdown).

**Not yet enforced** (tracked separately):

- `boundary` layer (diff-vs-allowed-paths) is implemented upstream but
  has a path-handling bug on non-linux runners in 0.2.7 that we are
  avoiding until the next release. Boundaries are still declared in
  specs — we just don't block merges on them yet.
- `verify` layer (scenario-level pass/fail) needs an AI backend we
  have not wired up. Scenarios lint-pass when they bind a real test
  via the `Test:` selector, but the tool can't yet confirm the test
  exists or passes.

When the upstream fix lands we flip from `lint` to
`lifecycle --layers lint,boundary`; the AI verify layer is a separate
future issue.

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
script prints a friendly notice and succeeds, and CI enforces the
check on every PR.

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

# 4. Verify locally (full lifecycle)
just check-specs

# 5. Before PR: ensure `just check` is green (it runs check-specs too)
just check && just test
```

Pre-commit runs `agent-spec lint` automatically on any staged
`.spec` file.

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
