# agent-spec — BDD Contracts for Agent Work

Canonical tool: [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec).
An AI-native BDD verification framework. Humans author a **task contract**,
the agent implements against it, `agent-spec` verifies compliance
deterministically.

**In yaya, every non-trivial feature PR is backed by a `.spec.md` contract.**
Trivial means: single-line typos, doc-only changes, dependency bumps. Everything
else — new commands, new `core/` modules, new agents, behavior changes —
must have a spec.

## Install

```bash
cargo install agent-spec
```

Also install the appropriate agent skill for your runtime:

- Claude Code ⇒ `agent-spec-tool-first` (under `~/.claude/skills/`)
- Codex / Cursor / Aider ⇒ matching skill per the upstream README

## Task contract shape (`.spec.md`)

Contracts live at `specs/<slug>.spec.md`, one per issue:

```markdown
# specs/<slug>.spec.md

## Intent
What and why — one paragraph, stakeholder-readable.

## Decisions
Fixed technical choices that are NOT up for debate in this PR.
(Library picks, file layout, algorithmic direction.)

## Boundaries
- Allowed: `src/yaya/cli/commands/<new>.py`, `tests/cli/test_<new>.py`
- Forbidden: `src/yaya/core/updater.py`, `pyproject.toml` dependencies section

## Completion Criteria (BDD)
Scenario: happy path
  Given <precondition>
  When  <action>
  Then  <observable outcome>
  Test: tests/cli/test_<new>.py::test_<scenario>

Scenario: error case
  Given <precondition>
  When  <invalid input>
  Then  <error code + JSON suggestion field>
  Test: tests/cli/test_<new>.py::test_<scenario_error>
```

Minimum 3 scenarios: happy path + error + edge case.
Every scenario MUST have an explicit `Test:` selector naming the test
function that proves it.

## Developer workflow

```bash
# 1. Author contract (or accept one written by a design agent)
$EDITOR specs/<slug>.spec.md

# 2. Pull context before coding (contract + codebase sketch)
agent-spec plan specs/<slug>.spec.md

# 3. Implement inside the issue worktree

# 4. Verify locally — lint + BDD compliance + report
agent-spec lifecycle specs/<slug>.spec.md

# 5. PR description from the contract
agent-spec explain specs/<slug>.spec.md > /tmp/pr-body.md
gh pr create --body-file /tmp/pr-body.md
```

CI runs `agent-spec guard` on staged changes — boundary violations or
unbound scenarios fail the build.

## Contract authoring rules

- **Intent is stakeholder-readable.** No code, no jargon the user didn't use.
- **Decisions are _decisions_, not suggestions.** If it's still open, it
  does not belong here — brainstorm elsewhere.
- **Boundaries are narrow.** Prefer listing explicit allowed paths. A spec
  that "allows everything" is useless for `guard`.
- **Scenarios are observable.** Assert on exit code, stdout JSON shape,
  file-on-disk state — never on "the function was called".
- **One contract, one issue.** Split large work into stacked contracts;
  see [stacked-prs](workflow.md#stacked-prs).

## Relationship to the org BDD issue template

`rararulab/.github/ISSUE_TEMPLATE/bdd_task.yml` captures the same four
sections (Description / Plan Spec / Feature file / Design spec). Use the
issue template to open the issue; copy its Gherkin block into
`specs/<slug>.spec.md` so `agent-spec` can verify it.

## What NOT To Do

- Do NOT open a feature PR without a `.spec.md` (trivia excepted).
- Do NOT change scope mid-PR without updating the contract in the same commit.
- Do NOT write scenarios without a `Test:` selector — `guard` will flag them
  as unbound.
- Do NOT relax `Boundaries` to make the build pass — revisit scope instead.
- Do NOT skip `agent-spec lifecycle` before requesting review.
