# Wiki Log

Append-only chronological record of ingests, queries, and lint
passes. Never rewrite history; append only. Entry prefix is
`## [YYYY-MM-DD] <kind> | <title>` so `grep "^## \[" log.md`
parses.

## [2026-04-17] ingest | Karpathy — LLM Wiki (gist 442a6bf)
Adopted the three-layer pattern (raw sources / wiki / schema) for
`docs/wiki/`. Added `AGENT.md`, `index.md`, this log, and
`sources/karpathy-llm-wiki.md`. Kept the existing
`lessons-learned.md` in place as the first `lessons/*` page.
See: sources/karpathy-llm-wiki.md, AGENT.md

## [2026-04-17] ingest | BMAD-METHOD (bmad-code-org/BMAD-METHOD)
Adopted two disciplines: the 4-phase gate model (analysis → plan →
solutioning → implementation, gated on explicit artifacts) and
explicit HALT conditions (no stopping at "milestones" or "natural
breakpoints"). Both codified in `docs/dev/workflow.md`. Rejected
the full installer, XML workflow DSL, sprint-status YAML tracker,
party mode, and the BMB/TEA/BMGD/CIS sibling modules — out of
scope for yaya. Retrospective ceremony merged into the existing
Karpathy wiki lint operation; no duplicate cadence.
See: sources/bmad-method.md, ../dev/workflow.md
