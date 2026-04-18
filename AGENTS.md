# AGENTS.md — yaya

Codex entry point. Read order:
1. [GOAL.md](GOAL.md) — product anchor. Every scope decision checks here first.
2. [AGENT.md](AGENT.md) — canonical agent spec.

## Communication
- 用中文与用户交流。代码、注释、commit、文档全部英文。

## Authoritative anchors
@GOAL.md
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

## Codex-specific

- Before any code edit: verify you are inside a worktree under `.worktrees/issue-{N}-*`.
  If you're on `main`, stop and open an issue first.
- yaya carries local experimental issue and PR templates under `.github/`; keep
  them aligned with the org baseline unless an issue explicitly tests workflow
  changes.
- Before reporting done: run `just check && just test` in the worktree, then
  `gh pr checks {PR} --watch` and paste the green summary.
- Use subagents for independent issues in parallel; one worktree per subagent.
- BDD contracts via [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec)
  are non-negotiable for non-trivial feature work: author `specs/<slug>.spec`,
  run `scripts/check_specs.sh` before commit, and let CI run the same lifecycle
  wrapper plus `scripts/check_feature_sync.py`.
- Python code follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html);
  every public symbol has a Google-style docstring.
