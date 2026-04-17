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

## Phase gates (yaya's flavour of the BMAD 4 phases)

Every non-trivial change passes through four phases **in order**. You
cannot skip a phase; each one produces the artifact the next consumes.
Pattern distilled from [BMAD-METHOD](../wiki/sources/bmad-method.md);
full rationale lives in that wiki source page.

| # | Phase | yaya artifact | Where it lives |
|---|---|---|---|
| 1 | **Analysis** | the issue itself, with a GOAL.md-anchored description and the right labels | GitHub issue |
| 2 | **Plan** | a task contract with Intent / Decisions / Boundaries / Completion Criteria | `specs/<slug>.spec` (agent-spec) |
| 3 | **Solutioning** | the design call — which events, which plugin category, which files; cite the authoritative doc | PR description + updates to `docs/dev/*.md` and folder `AGENT.md` |
| 4 | **Implementation** | code, tests, `just check && just test` green, CI green | worktree branch → PR |

Gate checks (answer `yes` before moving to the next phase):

1. **Analysis → Plan**: Does the issue state *what* and *why* against
   a GOAL.md principle? Are all required labels attached?
2. **Plan → Solutioning**: Does the `specs/<slug>.spec` lint clean
   (`agent-spec lint`)? Is every Completion Criteria scenario bound
   to a concrete test via `Test:`?
3. **Solutioning → Implementation**: Does the design cite the
   authoritative `docs/dev/*.md` for every new contract it touches?
   Are dependency issues resolved (no forward references to unmerged
   PRs in unrelated streams)?
4. **Implementation → merge**: `just check && just test` green,
   `gh pr checks --watch` green, spec still reflects the shipped code?

Trivial changes — single-line typos, dependency bumps, doc-only
clarifications — skip phases 2 and 3. Anything else does not.

## HALT conditions — when to stop mid-implementation

Borrowed verbatim from BMAD's developer-workflow rule:

> Do NOT stop because of "milestones", "significant progress", or
> "session boundaries". Continue in a single execution until the
> story is complete UNLESS a HALT condition is triggered or the user
> gives other instructions.

The rule exists because LLMs hallucinate natural breakpoints and
lose context on resume. Do not do this.

HALT for any of these; otherwise keep going:

- **Scope creep**: the work is diverging from the `specs/<slug>.spec`
  Boundaries. HALT → amend the spec in the same PR or open a
  follow-on issue.
- **Failing test you cannot diagnose**: HALT → file the failure
  (paste `turn_id` + `session_id` per `docs/dev/debug.md`) and ask
  the user.
- **Unresolved dependency**: the implementation blocks on an issue
  whose PR has not landed. HALT → comment on the PR and wait.
- **Authorization-required action**: you are about to force-push,
  rewrite main, or publish a release the user did not ask for.
  HALT always.
- **Explicit user "stop"**: HALT immediately.

Do NOT HALT for:

- Reaching a "natural milestone", a "good checkpoint", or a
  "significant chunk" — continue.
- Wanting a review between layers — finish the spec's Completion
  Criteria, then open the PR.
- Hitting a failed pre-commit hook — fix it, re-stage, continue.

Scheduling a "next session" counts as HALTing. Do not do it unless
one of the HALT conditions above applies.

## Retrospective cadence

Retrospective is the wiki lint pass defined in
[docs/wiki/AGENT.md](../wiki/AGENT.md#lint). Runs every ~5 merged
PRs or every two weeks, whichever comes first.

Each retro appends a `## [YYYY-MM-DD] lint | retrospective` entry to
`docs/wiki/log.md` and updates `docs/wiki/lessons-learned.md` with
any new hazards. Output is a short list of:

- Contradictions found between wiki pages.
- Stale claims about sources (`vendor/*`, external gists) that
  newer commits have superseded.
- Orphan wiki pages (zero inbound links) to delete or link.
- Missing concept pages where design discussions repeatedly cite
  the same idea without a home.
