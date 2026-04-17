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
- **Component**: `core` | `cli` | `ci` | `docs`

Issue and PR templates are inherited from `rararulab/.github` — do not
duplicate them in this repo.

## Multi-agent dispatch

See [multi-agent.md](multi-agent.md).

## Stacked PRs

For features > ~400 LOC or crossing layers, follow
`rararulab/.github/docs/stacked-prs.md`.
