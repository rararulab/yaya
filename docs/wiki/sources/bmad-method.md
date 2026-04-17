# BMAD-METHOD — Breakthrough Method for Agile AI-Driven Development

- **URL / path**: <https://github.com/bmad-code-org/BMAD-METHOD>
- **Consulted on**: 2026-04-17
- **Commit / version**: default branch head on consult date (45k stars,
  under active development, v6 series)
- **Status**: active

## Why we read it

The user asked us to learn from BMAD's AI-development workflow.
BMAD structures LLM-driven software work as explicit agile ceremonies
(analyst, PM, architect, developer, retrospective) with state files
and gated phases. At ~45k stars it is the most-adopted community
framework for this; worth stripping for parts even though yaya will
not install it.

## What we took

### 1. Four-phase gate discipline

BMAD's BMM module splits work into four numbered phases, each
producing specific artifacts:

1. **Analysis** (`1-analysis/`) — analyst, tech-writer, product brief,
   PR/FAQ, research.
2. **Plan** (`2-plan-workflows/`) — PM, UX designer, create-PRD,
   edit-PRD, validate-PRD.
3. **Solutioning** (`3-solutioning/`) — architect,
   create-architecture, create-epics-and-stories,
   generate-project-context, check-implementation-readiness.
4. **Implementation** (`4-implementation/`) — dev, create-story,
   dev-story, code-review, retrospective, sprint-planning,
   sprint-status.

You cannot skip phases; earlier phases produce the artifacts later
phases consume. We already have pieces of this implicitly — GOAL.md
is analysis, `specs/<slug>.spec` is plan, `docs/dev/architecture.md`
+ `plugin-protocol.md` are solutioning, the worktree + PR is
implementation — but the gates are not explicit. **Adopted as
`docs/dev/workflow.md` phase gates.**

### 2. Context-filled story spec

BMAD insists every implementation task starts from a "context-filled
story spec file" — a standalone document with goal, ACs, constraints,
prior art, and a task list the dev agent checks off. The dev agent
never pieces context together from chat history.

We already have the scaffold for this via agent-spec
(`specs/<slug>.spec` with Intent / Decisions / Boundaries /
Completion Criteria). **Adopted with no changes; BMAD's pattern
confirms the existing direction.**

### 3. HALT conditions + "don't stop at milestones"

BMAD's `bmad-dev-story/workflow.md` contains verbatim:

> Absolutely DO NOT stop because of "milestones", "significant
> progress", or "session boundaries". Continue in a single execution
> until the story is COMPLETE (all ACs satisfied and all
> tasks/subtasks checked) UNLESS a HALT condition is triggered or
> the USER gives other instruction.

Explicit HALT actions fire on concrete conditions:
`HALT - Run create-story`, `HALT - User can review sprint status`,
etc. This codifies a failure mode we have observed: the LLM hitting
a natural breakpoint and pausing work, losing context on resume.
**Adopted as an explicit HALT list in `docs/dev/workflow.md`.**

### 4. Retrospective as a ceremony, not an afterthought

BMAD makes retrospective a first-class skill
(`bmad-retrospective/`) that runs on a cadence and produces durable
artifacts. Combines cleanly with the Karpathy wiki lint pass we
already adopted: one ceremony covers both "what did we learn" and
"is the wiki still accurate". **Adopted by tying the retro cadence
to the wiki lint operation.**

## What we rejected

### The whole-framework install

BMAD ships via `npx bmad-method install` and drops ~30 skills, four
modules (BMM / BMB / TEA / BMGD / CIS), a node-based installer, and
a config file (`_bmad/bmm/config.yaml`) into your project. That is
an entire opinionated platform. yaya is a kernel-style agent
project; adopting BMAD's installer would bury yaya under BMAD.

### XML-tagged workflow DSL

BMAD authors each skill as a markdown file with XML tags
(`<workflow>`, `<step n="1">`, `<check if="...">`, `<goto
anchor="...">`, `<action>`, `<critical>`, `HALT`). This is a
small DSL on top of markdown that BMAD's runtime interprets. yaya
already has agent-spec for structured task contracts and plain
prose elsewhere; a second DSL is churn for marginal gain.

### Communication-language / user-skill-level configuration

BMAD's dev workflow branches on `{communication_language}` and
`{user_skill_level}` configured in `_bmad/bmm/config.yaml`. yaya
has exactly one stance (Chinese with the user, English in artifacts)
and one audience (the user + AI agents operating the repo). No
runtime branching needed.

### sprint-status.yaml as a parallel tracker

BMAD maintains `sprint-status.yaml` listing every story with status
(`ready-for-dev`, `in-progress`, `done`). It exists because BMAD is
agnostic to your issue tracker. yaya uses GitHub issues as the
single source of truth; a parallel YAML file is redundant. We take
the *concept* — before picking up work, confirm the issue has full
context — without the extra file. GitHub labels and issue comments
carry the equivalent state.

### "Party mode" — multi-persona single-session collaboration

BMAD's `bmad-party-mode` summons analyst + PM + architect + dev
personas into one chat to deliberate. yaya has sub-agents via the
kernel's `agent` tool (#34) for this; adding a single-session
multi-persona shim in prose would fight the infrastructure.

### BMAD Builder / Test Architect / Game Dev / CIS modules

Out of scope. yaya is a single project with a fixed kernel; not a
general-purpose workflow framework.

## Mapping to yaya's existing artifacts

| BMAD concept | yaya equivalent | Status |
|---|---|---|
| Analysis phase | `GOAL.md` + relevant `docs/dev/*.md` | exists |
| Plan phase (PRD) | `specs/<slug>.spec` via agent-spec | exists, lint in CI |
| Solutioning phase (architecture) | `docs/dev/plugin-protocol.md` + folder `AGENT.md` | exists |
| Implementation phase | issue → worktree → PR state machine | exists |
| Story spec | `specs/<slug>.spec` | exists |
| HALT conditions | **new** — now in `docs/dev/workflow.md` | adopted |
| Phase gates | **new** — now in `docs/dev/workflow.md` | adopted |
| Retrospective | wiki lint pass (see `docs/wiki/AGENT.md`) | merged |
| Sprint status tracker | GitHub issues + labels | equivalent |

## See also

- [docs/dev/workflow.md](../../dev/workflow.md) — phase gates + HALT
  conditions live here, not in the wiki.
- [docs/dev/agent-spec.md](../../dev/agent-spec.md) — the spec layer
  BMAD calls "context-filled story spec".
- [docs/wiki/AGENT.md](../AGENT.md) — wiki schema; retrospective is
  the lint operation defined there.
- [sources/karpathy-llm-wiki.md](karpathy-llm-wiki.md) — sibling
  source page; BMAD's retro ↔ Karpathy's lint share the same shape.
