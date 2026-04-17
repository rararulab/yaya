# CLAUDE.md — yaya

Claude Code entry point. The canonical agent spec is `AGENT.md` — read it first.

## Communication
- 用中文与用户交流。代码、注释、commit、文档全部英文。

## Authoritative rules
@AGENT.md

## Org baseline (rararulab/.github)
These live in the org `.github` repo and apply to every rararulab repo. Fetch via
`gh api repos/rararulab/.github/contents/<path>` when you need the exact text:

- `docs/workflow.md` — issue → worktree → PR state machine (MANDATORY).
- `docs/stacked-prs.md` — large-feature decomposition.
- `docs/commit-style.md` — Conventional Commits with `(#N)` + `Closes #N`.
- `docs/code-comments.md` — English only; public items documented; no drive-by comments.
- `docs/agent-md.md` — every package needs an `AGENT.md`.
- `docs/anti-patterns.md` — org-wide "do not" list (Rust-specific items don't apply; workflow items do).

## Claude-Code-specific

- Before any code edit: verify you are inside a worktree under `.worktrees/issue-{N}-*`.
  If you're on `main`, stop and open an issue first.
- Before reporting done: run `just check && just test` in the worktree, then
  `gh pr checks {PR} --watch` and paste the green summary.
- Use subagents for independent issues in parallel; one worktree per subagent.
- Oracle Agent Spec conformance (<https://github.com/oracle/agent-spec>) is
  non-negotiable for anything under `src/yaya/core/` that defines an agent or flow.
