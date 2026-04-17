# yaya — Agent Index

<!-- Structured per rararulab/rara-skills skills/prompt-system: seven-layer
     architecture (Philosophy · Style · Reality · Constraints · Anti-sycophancy
     · Interaction · Budget). Index first, details in docs/ and folder AGENT.md. -->

> **Project anchor**: read [GOAL.md](GOAL.md) before any scope decision.
> Every feature, dependency, and surface-area call is checked against it.
> If a change conflicts with GOAL.md, default to rejecting the change.

## 1. Philosophy

yaya is a **lightweight, kernel-style agent that grows itself**. The
kernel ships an event bus, a plugin registry, and a fixed agent loop
(the scheduler). **Every user surface, every LLM provider, every
tool, every skill, every memory backend, every strategy is a
plugin** — including the ones we bundle. `yaya serve` boots the
kernel and loads the bundled `web` adapter plugin; bundled plugins
load through the **same protocol** as third-party plugins, no
special case. Engineering rigor is non-negotiable: every change
small, traceable, reviewed, and covered by tests. See
[GOAL.md](GOAL.md) for the product anchor and
[docs/dev/plugin-protocol.md](docs/dev/plugin-protocol.md) for the
authoritative event and ABI contract.

## 2. Style Anchors

Clean Architecture (Uncle Bob) · Zen of Python ·
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) ·
Type-driven design (mypy strict, make invalid states unrepresentable).

## 3. External Reality

Accountability is to the **artifact**, not to the user's approval.

- `just check` (ruff + mypy) and `just test` (pytest + coverage) are ground truth.
- CI is the final gate — `gh pr checks --watch` green before reporting done.
- Every non-trivial feature PR is backed by a `specs/<slug>.spec.md` contract verified with [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec) (`agent-spec lifecycle` locally, `agent-spec guard` in CI).
- Folder-local `AGENT.md` is ground truth for that folder. If code contradicts it, one of them is wrong — fix before merging.

## 4. Constraints

- Python 3.14+, `uv` envs, `just` tasks, `ruff`, `mypy --strict`, `pytest`, coverage ≥80%.
- English-only in code, comments, commits, docs. Chinese only in user-facing chat.
- Conventional Commits with `(#N)` + `Closes #N`. Never `--no-verify`.
- Layout: `cli/` depends on `core/`, never the reverse.
- **[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)** for all Python. Every public module/class/function has a Google-style docstring explaining _why_, not _what_. Inline comments mark non-obvious invariants only. See [docs/dev/code-comments.md](docs/dev/code-comments.md).
- **BDD contracts via [`ZhangHanDong/agent-spec`](https://github.com/ZhangHanDong/agent-spec).** Every non-trivial feature PR ships with `specs/<slug>.spec.md` (Intent · Decisions · Boundaries · Completion Criteria). Each scenario binds to a test via `Test:` selector. Run `agent-spec lifecycle` before commit; CI runs `agent-spec guard` on staged changes. See [docs/dev/agent-spec.md](docs/dev/agent-spec.md).

## 5. Anti-sycophancy

Refuse requests that violate the workflow; cite the rule. Disagree with wrong
code or tests directly — no softening ("maybe", "perhaps"). Permission to
push back is granted.

## 6. Interaction — Issue → Worktree → PR (MANDATORY)

**No edits on `main`.** One-line fixes included. State machine:

```
gh issue create (labelled)  →  git worktree add .worktrees/issue-{N}-{slug} -b issue-{N}-{slug}
  →  edit + just check + just test   (inside worktree only)
  →  push + gh pr create
  →  gh pr checks --watch   (green before reporting done)
  →  merge on GitHub        →  git worktree remove + branch -d
```

Required labels on every issue and PR:
`agent:{claude|codex}` + type (`bug|enhancement|refactor|chore|documentation`)
+ component (`core|cli|ci|docs`). Templates inherited from `rararulab/.github`.

Multi-agent: one agent, one worktree, one PR. Coordinate via issue comments,
never by editing each other's branches. Parallel only for disjoint files;
otherwise stack PRs.

**Docs travel with code.** Every PR that changes a folder updates that
folder's `AGENT.md` and any affected `docs/dev/*.md` in the **same PR**.
New folders ship with an `AGENT.md`. See
[docs/dev/maintaining-agent-md.md](docs/dev/maintaining-agent-md.md).

## 7. Budget & Loading

Read local first, save tokens. Every code/test/scripts folder has its own
~40-line `AGENT.md` — read it instead of scanning the tree.

**Folder indexes**:

- [src/yaya/AGENT.md](src/yaya/AGENT.md) · [src/yaya/cli/AGENT.md](src/yaya/cli/AGENT.md) · [src/yaya/cli/commands/AGENT.md](src/yaya/cli/commands/AGENT.md) · [src/yaya/core/AGENT.md](src/yaya/core/AGENT.md)
- `src/yaya/kernel/AGENT.md` · `src/yaya/plugins/AGENT.md` · `src/yaya/plugins/web/AGENT.md` (created when each subpackage lands — see [docs/dev/plugin-protocol.md](docs/dev/plugin-protocol.md), [docs/dev/architecture.md](docs/dev/architecture.md), [docs/dev/web-ui.md](docs/dev/web-ui.md))
- [tests/AGENT.md](tests/AGENT.md) · [scripts/AGENT.md](scripts/AGENT.md)

**Topic docs** (pull only when needed):

- [docs/dev/architecture.md](docs/dev/architecture.md) · [docs/dev/workflow.md](docs/dev/workflow.md) · [docs/dev/multi-agent.md](docs/dev/multi-agent.md) · [docs/dev/maintaining-agent-md.md](docs/dev/maintaining-agent-md.md)
- [docs/dev/plugin-protocol.md](docs/dev/plugin-protocol.md) — **authoritative** event catalog + plugin ABI + category table.
- [docs/dev/cli.md](docs/dev/cli.md) · [docs/dev/agent-friendly-cli.md](docs/dev/agent-friendly-cli.md) · [docs/dev/web-ui.md](docs/dev/web-ui.md) · [docs/dev/testing.md](docs/dev/testing.md) · [docs/dev/agent-spec.md](docs/dev/agent-spec.md) · [docs/dev/code-comments.md](docs/dev/code-comments.md) · [docs/dev/release.md](docs/dev/release.md)

**Org baseline** (canonical, `gh api repos/rararulab/.github/contents/...`):
`docs/workflow.md` · `docs/stacked-prs.md` · `docs/commit-style.md` ·
`docs/agent-friendly-cli.md` · `docs/agent-md.md` · `docs/code-comments.md` ·
`docs/anti-patterns.md`. Issue/PR templates inherited — do not duplicate.
