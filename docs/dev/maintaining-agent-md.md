# Maintaining AGENT.md Files (MANDATORY)

Every significant folder has an `AGENT.md`. Agents read these **instead of**
re-scanning the whole tree. A stale `AGENT.md` wastes the next agent's tokens
and erodes trust in the index.

## Why per-folder AGENT.md

- **Token locality**: an agent touching `src/yaya/cli/commands/` reads
  `commands/AGENT.md` (≤40 lines) instead of skimming every file in the tree.
- **Surface compression**: architectural invariants are stated once, next to
  the code they constrain.
- **Drift detection**: if reality contradicts the local `AGENT.md`, one of
  them is wrong — fix before merging.

## The Rule

**If your PR changes behavior, structure, or invariants of a folder, you
MUST update that folder's `AGENT.md` and any affected `docs/dev/*.md` in the
same PR.** No follow-ups.

### What counts as a trigger

| You did this | You MUST update |
|---|---|
| Added a new file/module | The folder's `AGENT.md` (Architecture section) |
| Changed a public API / signature | Folder `AGENT.md` + any doc referencing it |
| Added a new invariant or constraint | Folder `AGENT.md` (Invariants) |
| Discovered an anti-pattern during review | Folder `AGENT.md` (What NOT To Do) |
| Added a new subfolder with code or tests | Create `<folder>/AGENT.md` — the PR cannot merge without it |
| Added/changed a CLI command | `src/yaya/cli/commands/AGENT.md` + `docs/dev/cli.md` |
| Added an agent/flow | `src/yaya/core/AGENT.md` + `docs/dev/agent-spec.md` + round-trip test |
| Changed test conventions/fixtures | `tests/AGENT.md` + `docs/dev/testing.md` |
| Changed build/release tooling | `scripts/AGENT.md` + `docs/dev/release.md` |

## Shape — prompt-system seven-layer architecture (MANDATORY)

Every `AGENT.md` follows the
[rara-skills prompt-system framework](https://github.com/rararulab/rara-skills/blob/main/skills/prompt-system/SKILL.md):
seven layers with specific functions. **Root `AGENT.md` uses all seven.**
**Folder-local `AGENT.md` uses five** (Philosophy, External Reality,
Constraints, Interaction, Budget & Loading) and inherits Style Anchors +
Anti-sycophancy from root.

Target sizes: root ~600–1000 tokens; folder-local ~200–400 tokens (≤60
lines). Over 1500 tokens requires justification.

### Root template

```markdown
## 1. Philosophy          (≤50 tok, conceptual anchor)
## 2. Style Anchors       (60–100 tok, 2–3 stylistic references)
## 3. External Reality    (60–100 tok, accountability to artifact)
## 4. Constraints         (90–150 tok, mechanical configuration)
## 5. Anti-sycophancy     (30–50 tok, permission to disagree)
## 6. Interaction         (150–250 tok, state machine + patterns)
## 7. Budget & Loading    (pointers — read local first)
```

### Folder-local template

```markdown
<!-- Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy           One sentence: what lives here and why.
## External Reality     What verifies success (tests, CI, contracts).
## Constraints          File layout, invariants, dependencies.
## Interaction          Patterns to follow + "Do NOT X — because Y".
## Budget & Loading     Links to sibling AGENT.md and relevant docs/dev/*.md.
```

Only include what an agent **cannot** infer from reading the code.

### Health metrics (per the framework)

- Rules-to-anchors ratio < 3:1 (no rule walls).
- Filler below 2% (cut "obviously", "simply", "just").
- Coverage ≥5/7 layers for root; ≥5/5 for folder-local template.
- Few-shot examples (if any) 100% annotated.

## PR checklist

Before opening the PR:

- [ ] For every folder you touched, its `AGENT.md` is still accurate.
- [ ] New folders ship with an `AGENT.md`.
- [ ] If you changed a public contract, the corresponding `docs/dev/*.md`
      reflects the change.
- [ ] If you added an agent/flow, the Agent Spec round-trip test is present.
- [ ] `just docs-test` passes (no broken doc links).

Reviewers reject PRs that skip this.
