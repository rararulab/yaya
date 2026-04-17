# yaya — Agent Index

Structured per `rararulab/rara-skills/skills/prompt-system`. This file is an
**index**, not a manual. Read only the doc that applies to your task.

## Read first

- [docs/architecture.md](docs/architecture.md) — layout, layering, AGENT.md rule.
- [docs/workflow.md](docs/workflow.md) — issue → worktree → PR (MANDATORY).
- [docs/multi-agent.md](docs/multi-agent.md) — parallel dispatch, hand-off rules.
- [docs/cli.md](docs/cli.md) — command pattern, JSON shape, extension checklist.
- [docs/testing.md](docs/testing.md) — `just check` / `just test`, TDD, coverage.
- [docs/agent-spec.md](docs/agent-spec.md) — Oracle Agent Spec conformance.

## Org baseline (canonical, fetch via `gh api repos/rararulab/.github/...`)

- `docs/workflow.md` · `docs/stacked-prs.md` · `docs/commit-style.md`
- `docs/agent-friendly-cli.md` · `docs/agent-md.md` · `docs/code-comments.md`
- `docs/anti-patterns.md` (Rust-specific items do not apply; workflow items do)
- `ISSUE_TEMPLATE/*` and `pull_request_template.md` are inherited — do not duplicate.

## Non-negotiables (read even if you skip everything else)

- **No edits on `main`.** All changes go through issue → worktree → PR. One-line fixes included.
- **Labels required** on every issue and PR: `agent:{claude|codex}` + type + component.
- **Tests before merge.** `just check && just test` green; coverage must not regress.
- **Agent Spec conformance** for anything under `src/yaya/core/` that defines an agent/flow.
- **English-only** in code, comments, commits, docs. Chinese only in user-facing chat.
- **Never `--no-verify`.** Fix the hook, don't bypass it.

## Style anchors

Clean Architecture · Zen of Python · Type-driven design (mypy strict).

## Anti-sycophancy

If a request violates the workflow, refuse and cite the specific rule.
Disagree with wrong code/tests directly — no softening.
