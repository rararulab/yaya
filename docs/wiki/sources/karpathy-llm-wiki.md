# Karpathy — LLM Wiki

- **URL / path**: <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
- **Consulted on**: 2026-04-17
- **Commit / version**: gist as retrieved on 2026-04-17
- **Status**: active

## Why we read it

Decide the shape of `docs/wiki/`. We already had a single
`lessons-learned.md` file; we needed a pattern that scales as the
repo accumulates design notes, decision history, and material
extracted from vendor repos (`vendor/kimi-cli`, `vendor/bub`) and
external references.

## What we took

The three-layer architecture verbatim:

1. **Raw sources (immutable)** — for yaya this maps to
   `vendor/kimi-cli`, `vendor/bub`, external gists and articles,
   the homework PDF. Agents read; they never modify.
2. **The wiki** — `docs/wiki/` — LLM-authored, maintained markdown
   pages that compile knowledge extracted from sources.
3. **The schema** — `docs/wiki/AGENT.md` — tells agents how to
   maintain the wiki.

The three operations (ingest / query / lint), the two special
files (`index.md`, `log.md`), and the log-entry prefix convention
(`## [YYYY-MM-DD] <kind> | <title>`) — all adopted.

Page shapes (source / concept / decision / lesson) in
`docs/wiki/AGENT.md` are yaya-specific instantiations of the "pick
what's useful" clause at the end of the gist.

## What we rejected

- **Obsidian / Marp / Dataview tooling.** yaya's audience is
  engineers reading on GitHub or in their editor; Obsidian-specific
  extensions would fragment the experience.
- **Browser extensions (Web Clipper) and image handling.** Our
  sources are code repos, text docs, and specs — no images to
  download, no web clipper needed.
- **Dedicated search engine (qmd).** At current scale `index.md`
  is enough. Revisit when the wiki crosses ~100 pages.
- **Dataview / YAML frontmatter metadata.** Premature. Markdown +
  relative links suffices until we have a concrete query the plain
  text can't answer.

## See also

- [AGENT.md](../AGENT.md) — the wiki schema this gist inspired.
- [index.md](../index.md) — catalog of wiki pages.
- [log.md](../log.md) — the chronological log this gist convention
  defines.
