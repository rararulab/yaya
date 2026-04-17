# yaya — Agent Index

Structured per `rararulab/rara-skills/skills/prompt-system`. This file is an
**index**, not a manual. Read only the doc that applies to your task.

## Read first (development docs)

- [docs/dev/architecture.md](docs/dev/architecture.md) — layout, layering, AGENT.md rule.
- [docs/dev/workflow.md](docs/dev/workflow.md) — issue → worktree → PR (MANDATORY).
- [docs/dev/multi-agent.md](docs/dev/multi-agent.md) — parallel dispatch, hand-off rules.
- [docs/dev/cli.md](docs/dev/cli.md) — command pattern, JSON shape, extension checklist.
- [docs/dev/testing.md](docs/dev/testing.md) — `just check` / `just test`, TDD, coverage.
- [docs/dev/agent-spec.md](docs/dev/agent-spec.md) — Oracle Agent Spec conformance.
- [docs/dev/agent-friendly-cli.md](docs/dev/agent-friendly-cli.md) — org CLI spec snapshot.
- [docs/dev/release.md](docs/dev/release.md) — release-please flow.

## User-facing docs (skip unless you need them)

- [docs/guide/install.md](docs/guide/install.md)
- [docs/guide/usage.md](docs/guide/usage.md)

## Org baseline (canonical, fetch via `gh api repos/rararulab/.github/...`)

- `docs/workflow.md` · `docs/stacked-prs.md` · `docs/commit-style.md`
- `docs/agent-friendly-cli.md` · `docs/agent-md.md` · `docs/code-comments.md`
- `docs/anti-patterns.md` (Rust-specific items do not apply; workflow items do)
- `ISSUE_TEMPLATE/*` and `pull_request_template.md` are inherited — do not duplicate.

## Non-negotiables

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
