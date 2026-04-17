# docs/wiki — AGENT.md (wiki schema)

This directory is yaya's **LLM-maintained knowledge wiki**, following
Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
pattern. The wiki is **not** product documentation — `docs/dev/*`
remains the authoritative engineering docs. The wiki compiles
durable lessons, decision history, and notes from sources we consulted.

## Three layers

1. **Raw sources (immutable).** `vendor/kimi-cli`, `vendor/bub`,
   external gists, the homework PDF, issue comments. Agents read
   these; they never modify them.
2. **The wiki (this directory).** LLM-written pages that compile
   knowledge from sources. Every page has a single owner: the
   structure, not the human.
3. **The schema (this file).** How the wiki is organized, what
   each page shape looks like, when to update what.

## Directory structure

```
docs/wiki/
├── AGENT.md              <- this file
├── index.md              <- content catalog; updated on every ingest
├── log.md                <- append-only chronological log
├── sources/              <- one page per external source consulted
├── concepts/             <- design concepts we converged on
├── lessons/              <- rolling lessons from implementation (was lessons-learned.md)
└── decisions/            <- ADR-style records of major choices
```

Create subdirectories lazily when the first page of that type lands.

## Operations

### Ingest

Triggered when we consult a new source (vendor repo, external doc,
gist, paper, thread) for a non-trivial design decision.

1. Read the source.
2. Create a file under `sources/<slug>.md` using the **Source page
   shape** below. Keep the raw source immutable under `vendor/` or
   cite the URL.
3. If the source introduces a new concept, create or update the
   matching `concepts/<slug>.md`.
4. Cross-link: every new page links to at least one other wiki page.
5. Append an entry to `log.md`:
   ```
   ## [YYYY-MM-DD] ingest | <source title>
   <one line on what we took from it>
   See: sources/<slug>.md, concepts/<slug>.md
   ```
6. Update `index.md` with the new page listed under its category.

Convert relative dates the user mentions to absolute dates
(`"yesterday"` → `YYYY-MM-DD`) — the log is permanent.

### Query

Triggered when a design question recurs or an audit is performed.

1. Read `index.md` first to find relevant pages.
2. Drill into concept + source pages. Answer with citations.
3. If the answer is durable (an audit, a comparison, a non-obvious
   synthesis) and not already captured, file it as
   `concepts/<slug>.md` or `decisions/NNNN-<slug>.md`.
4. Append a short entry to `log.md`:
   ```
   ## [YYYY-MM-DD] query | <question>
   <one line on the answer>
   See: <pages>
   ```

Explorations that never get filed back are wasted effort. File them.

### Lint

Triggered periodically (every few weeks or before a major PR).

Check for:

- **Orphans** — pages with zero inbound links (they'll rot).
- **Stale claims** — "kimi does X" may no longer be true; re-check
  vendor/ commit hash referenced in the source page.
- **Contradictions** — two concept pages disagreeing on the same
  claim.
- **Missing cross-refs** — a concept mentioned in one page but never
  linked from related pages.
- **Gaps** — important yaya components with no wiki presence
  (`docs/dev/*.md` covers the spec; the wiki should cover the
  *why* and *lessons*).

Append to `log.md`:

```
## [YYYY-MM-DD] lint | <brief>
<N findings; resolved/deferred>
See: <pages touched>
```

## Page shapes

### Source page — `sources/<slug>.md`

```markdown
# <source title>

- **URL / path**: <vendor/kimi-cli, external URL, etc.>
- **Consulted on**: YYYY-MM-DD
- **Commit / version**: <git sha or version string when known>
- **Status**: active | archived

## Why we read it

One paragraph.

## What we took

Concrete patterns, code references, approved decisions. Cite
specific files with line numbers where relevant.

## What we rejected

Things in the source we deliberately did not copy, with reason.

## See also

- concepts/<slug>.md
- decisions/<slug>.md
```

### Concept page — `concepts/<slug>.md`

```markdown
# <concept>

One-paragraph definition.

## How it appears in yaya

Concrete types, files, events, plugin categories.

## Invariants

What must always be true.

## Sources

- sources/<slug>.md (primary inspiration)
- sources/<slug2>.md (pattern from)

## Related

- concepts/<other>.md
```

### Decision page — `decisions/NNNN-<slug>.md`

Standard ADR: Status / Context / Decision / Consequences.
Numbered sequentially; never renumber.

### Lesson page — `lessons/<slug>.md`

Rolling log of recurring review findings and hazards. Entry shape
lives inside the page itself — append only; never rewrite history.

## Conventions

- Pages are markdown only. No HTML.
- English only (matches root `AGENT.md`).
- Cross-references use relative paths
  (`[event bus](../concepts/event-bus.md)`).
- Dates are absolute `YYYY-MM-DD`.
- `index.md` and `log.md` are updated in the **same PR** that adds
  or modifies a wiki page.
- Never edit `log.md` retroactively — append only.
- No page exists without at least one inbound link from `index.md`.
