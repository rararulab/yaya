# Workflow (yaya addenda)

**Canonical spec**: `rararulab/.github/docs/workflow.md` — fetch with
`gh api repos/rararulab/.github/contents/docs/workflow.md --jq .content | base64 -d`.
Read it first. This file only lists yaya-specific additions.

## State machine (reminder)

```
issue → worktree → edit → just check && just test → push → PR → CI green → merge → cleanup
```

No edits on `main`. One-line fixes included.

## Worktree verification commands (yaya)

Inside `.worktrees/issue-{N}-{slug}`:

```bash
just check   # ruff lint + format check + mypy strict
just test    # pytest with coverage (must not drop)
```

Both must be green before `git push`.

## Labels (required)

- **Agent**: `agent:claude` | `agent:codex`
- **Type**: `bug` | `enhancement` | `refactor` | `chore` | `documentation`
- **Component**: `core` | `kernel` | `plugins` | `cli` | `ci` | `docs`

yaya carries local experimental issue and PR templates under `.github/` so
agent-oriented workflow changes can be tested here before they move to the org
baseline. Keep those templates aligned with `rararulab/.github` unless the
issue explicitly tests a workflow change.

## Issue as the task record

The GitHub issue is the only task record. Do not create shadow task files,
BMAD-style story files, local task databases, or `.agents/tasks/` records
unless the issue explicitly asks for a durable product artifact.

All agent-facing task context belongs in the issue body or issue comments.
Planner output goes in issue comments. Implementation notes go in the PR body.
Reviewer findings go in PR reviews or PR comments.

## Agent Task Packet

Every issue assigned to a coding agent MUST include an **Agent Task Packet**
section in the issue body or a linked issue comment. For small issues, a
terse packet is enough; for agent-runtime, plugin-protocol, prompt, or
security work, fill every subsection.

```markdown
## Agent Task Packet

### Read First
- `GOAL.md`
- `AGENT.md`
- Area-specific docs, specs, and folder `AGENT.md` files

### Expected Touch Points
- Files or folders the agent is expected to edit

### Forbidden / Avoid
- Files, layers, dependencies, or design moves that are out of scope

### Existing Patterns
- Local code, tests, docs, or prior PRs the agent should imitate

### Validation
- Exact commands the agent must run before pushing

### Failure Modes To Cover
- Error paths, rollback paths, security boundaries, and drift risks
```

The packet is not a replacement for a BDD spec. The issue packet tells the
agent how to work in this repository; `specs/<slug>.spec` defines the
observable behavior and test bindings for the change.

## PR as the implementation record

The local PR template is the baseline. For agent-authored PRs, fill the body
with these additional facts instead of creating a separate local note:

- **Issue context used**: issue number, Agent Task Packet source, and any
  issue comments that changed the plan.
- **Deviations from issue/spec**: every scope or design change discovered
  during implementation, with the GitHub comment or commit that explains it.
- **Agent notes**: reviewer-relevant implementation notes, known trade-offs,
  and any follow-up issue numbers.
- **Verification evidence**: exact local commands run and the CI check summary.

## Multi-agent dispatch

See [multi-agent.md](multi-agent.md).

## Stacked PRs

For features > ~400 LOC or crossing layers, follow
`rararulab/.github/docs/stacked-prs.md`.
