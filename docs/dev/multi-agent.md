# Multi-Agent Development

yaya is built by multiple agents (Claude, Codex, …) working in parallel.
Coordination happens through GitHub, not chat context.

## Rules

1. **One agent, one worktree, one branch, one PR.** Agents never share a
   working tree.
2. **Shared state is the issue tracker.** Discussion, status, and hand-off
   happen in issue/PR comments — `@claude`, `@codex` mentions route work.
3. **No cross-branch edits.** An agent never pushes to another agent's
   branch. Hand-off = comment + new PR.
4. **Parallelism condition**: issues are dispatched in parallel only when
   they touch disjoint files. If two issues overlap, serialize them or use
   stacked PRs (`rararulab/.github/docs/stacked-prs.md`).
5. **Identity is a label.** Every issue and PR carries `agent:claude` or
   `agent:codex` so authorship is auditable.

## Dispatch pattern

```
User request
  ├─ decompose into N independent issues (gh issue create …)
  ├─ for each issue i: spawn subagent in .worktrees/issue-{i}-{slug}
  ├─ subagents run in parallel, each opens its own PR
  └─ PRs reviewed + merged independently on GitHub
```

Large features that cannot be decomposed into independent issues use
**stacked PRs**: one epic issue, sub-issues branched off `feat/{name}`,
final summary PR to `main`.

## Done criteria (per PR)

- `just check` clean
- `just test` clean, coverage not regressed
- Agent Spec conformance test green if `core/` agents/flows changed
  (see [agent-spec.md](agent-spec.md))
- `gh pr checks {PR} --watch` green before reporting completion
