# yaya

[![CI](https://github.com/rararulab/yaya/actions/workflows/main.yml/badge.svg)](https://github.com/rararulab/yaya/actions/workflows/main.yml)
[![Release](https://img.shields.io/github/v/release/rararulab/yaya?sort=semver)](https://github.com/rararulab/yaya/releases/latest)
[![Docs](https://img.shields.io/badge/docs-rararulab.github.io%2Fyaya-blue)](https://rararulab.github.io/yaya/)

A **lightweight, kernel-style agent that grows itself.** `yaya serve`
boots an event-driven kernel and opens a chat in your browser; the
bundled `mercari_jp_search` tool lets the agent answer product
recommendation queries about Mercari Japan end-to-end.

## Overview

yaya is a local-first Python agent built around three small pieces:

- **Kernel** — an async event bus, a plugin registry, and a fixed agent
  loop (the scheduler). The kernel does not know about shopping,
  Mercari, or any specific LLM. It ships the minimum CLI
  (`serve` / `doctor` / `version` / `update` / `plugin`) and nothing else.
- **Plugins** — everything user-visible is a plugin, loaded through
  entry points and wired to the kernel bus. The bundled set covers one
  of each category: `web` adapter, `llm_openai` provider, `mercari_jp`
  tool, `strategy_react` strategy, `memory_sqlite` store.
- **Agent loop** — runs a classical ReAct cycle. The strategy plugin
  parses `Thought: / Action: / Action Input:` triples, the kernel
  dispatches tool calls through a typed `tool.call.request` envelope,
  and the loop injects the tool result back as an `Observation:` so
  the next LLM turn sees real data.

For the Mercari shopping job, the flow looks like this:

```
user message
  → strategy_react (ReAct system prompt + shopping output contract)
    → llm_openai (OpenAI-compatible endpoint, streaming)
      → mercari_jp_search (Mercapi-compatible POST to api.mercari.jp)
        → ToolOk envelope with 20 ranked candidates
      → llm_openai (ranks and writes a 3-row markdown recommendation)
  → web adapter (renders markdown + compact tool card)
```

No scraping, no browser automation, no login. The `mercari_jp` plugin
talks to Mercari's Mercapi-compatible JSON search endpoint with a
signed DPoP header — the same mechanism `take-kun/mercapi` uses.

Full product anchor: **[GOAL.md](GOAL.md)**.

## Setup

Requires **Python 3.14+**.

### Option 1 — install from a release wheel

```bash
pip install --upgrade \
  "yaya @ https://github.com/rararulab/yaya/releases/latest/download/yaya-py3-none-any.whl"
yaya version
```

Other targets — prebuilt binaries for Linux / macOS / Windows — live in
[docs/guide/install.md](docs/guide/install.md).

### Option 2 — from source (recommended for hacking)

```bash
git clone https://github.com/rararulab/yaya.git
cd yaya
uv sync              # creates .venv and installs project + dev deps
uv run yaya version  # verify the build
```

### Configure an LLM provider

The bundled `llm_echo` dev provider replies deterministically and needs
no keys, so `yaya serve` works out of the box. For real recommendations
point yaya at any OpenAI-compatible endpoint:

```bash
# OpenAI-native
export OPENAI_API_KEY=sk-...

# or an OpenAI-compatible endpoint (MiniMax / vLLM / LM Studio / Azure)
yaya config set providers.default.plugin   llm-openai
yaya config set providers.default.base_url "https://api.minimax.io/v1/"
yaya config set providers.default.model    "MiniMax-M2.7"
yaya config set providers.default.api_key  "${YOUR_KEY}"
yaya config set provider default
```

## Usage

```bash
yaya serve
```

This boots the kernel, loads all bundled plugins, and opens
`http://127.0.0.1:<port>/` in your browser. Type a request in the chat
and yaya will decide whether a tool call is needed.

### Example — Mercari product recommendation

Ask in natural language (English or Japanese, with or without
constraints):

> `Find me 3 iPhone 15 cases on Mercari JP under ¥1000, new condition, seller pays shipping.`

What yaya does:

1. `strategy_react` parses the ask; the LLM fills `mercari_jp_search`
   parameters — `keyword: "iPhone 15 case"`, `max_price_jpy: 1000`,
   `item_condition: "new"`, `shipping_payer: "seller"`.
2. The kernel dispatches a `tool.call.request` carrying
   `schema_version: "v1"` → `mercari_jp_search.run()` signs a DPoP
   request to `api.mercari.jp/v2/entities:search` and returns a
   `ToolOk` envelope with the top 20 ranked candidates.
3. The loop appends the envelope as an `Observation:` and lets the LLM
   produce a Final Answer.
4. The ReAct system prompt's **shopping output contract** forces the
   Final Answer into a strict markdown table:

   | Rank | Title | Price (JPY) | Condition | Why it fits | Link |
   |------|-------|-------------|-----------|-------------|------|
   | 1 | iPhone 15 Pro case clear TPU | ¥380 | 新品、未使用 | under ¥1000, new as requested, seller pays shipping | https://jp.mercari.com/item/… |
   | 2 | … | … | … | … | … |
   | 3 | … | … | … | … | … |

   Each `Why it fits` cell must cite at least one constraint you
   actually stated; generic "good price" or duplicate reasons across
   rows are rejected by the contract.

### Other commands

```bash
yaya doctor                     # boot check: bus round-trip + per-plugin health
yaya plugin list                # what's loaded right now
yaya --json doctor              # machine-readable shape for scripts
yaya session list               # persisted chat tapes
```

More examples in [docs/guide/usage.md](docs/guide/usage.md).

## Design Choices

### Kernel-style, not agent-framework

I considered LangChain / LlamaIndex / AutoGen / CrewAI / Semantic
Kernel and rejected them. They ship opinions about chains, flows,
memory, and orchestration that you cannot undo without forking. yaya
does the opposite: the kernel knows only events + plugins + a fixed
loop, and **everything else is a plugin** — even the web UI you use
to talk to it. If you don't like the ReAct strategy, drop in a
plan-execute strategy; the kernel won't notice the difference.

Hard ban in [AGENT.md §4](AGENT.md#4-constraints): no agent-framework
libraries are imported or vendored. Permitted LLM access is limited
to the official `openai` and `anthropic` SDKs.

### ReAct with a pinned output contract

For one-shot "search and recommend" flows, the model benefits from a
strict output shape more than from fancier orchestration. Two stacked
contracts live in
[`strategy_react`](src/yaya/plugins/strategy_react/plugin.py):

- **Thought / Final Answer split** — the prompt tells the model that
  the `Thought:` line is an internal scratchpad (the web UI folds it
  behind `Show reasoning`) and the `Final Answer:` line is the only
  user-visible surface. This stops the answer from being accidentally
  hidden inside Thought.
- **Shopping output contract** — when `mercari_jp_search` is in the
  tool registry, the prompt appends a block that hard-pins the final
  answer to a 3-row markdown table with a `Why it fits` cell that must
  cite a user-stated constraint, no duplicated reasons across rows.
  Without the tool, the contract is omitted so general chat is
  unaffected.

### Mercapi, not web scraping

The `mercari_jp` plugin does **not** use Playwright, BeautifulSoup, or
any scraping. It talks to the same JSON endpoint the Mercari mobile
app and `take-kun/mercapi` use — `POST
api.mercari.jp/v2/entities:search` with a DPoP-signed header. Upsides:
no CAPTCHA, no JS rendering, no "Access Denied" on HTML pages, stable
ranking signal, mock-testable in CI. Downsides: HTTP 403 / anti-bot
responses are terminal — we do not rotate identities or retry bypass;
the tool surfaces them as `ToolError(kind="rejected")`.

### v1 tool envelope

Tools return a typed `ToolOk(brief, display)` or
`ToolError(kind, brief, display)` envelope rather than free-form JSON.
The display block is one of `TextBlock` / `MarkdownBlock` /
`JsonBlock`. The agent loop projects the envelope into an
`Observation:` the LLM can actually parse, and the web UI renders each
tool call as a single compact card — a one-line header the user can
expand to see args + full output. No 20 KB JSON dumps in the
transcript.

### Filter-rich Mercari surface

`mercari_jp_search` exposes Mercari's native narrowing knobs:
`category_ids`, `brand_ids`, `item_condition`, `shipping_payer`, in
addition to `keyword` / `price` / `status` / `sort`. The LLM sees
these via the OpenAI function spec and picks them based on the user's
phrasing ("new", "under ¥1000", "送料込み").

### Session persistence

Every turn's events are written to a per-session tape (SQLite via
`memory_sqlite` for metadata; file store for the full transcript).
Reopening a chat hydrates from the tape and replays the frame sequence
through the same reducer the live WS path uses, so rehydrated sessions
look identical to the live run — tool cards, thought folds, and all.

## Potential Improvements

Honest gap list. These are real complaints I would not hand-wave at a
demo.

- **`mercari_jp_item_detail(item_id)`** — the search endpoint does not
  return seller rating, full description, shipping method, or ship-
  from region. A per-item detail fetch would unlock "prefer trusted
  sellers" and "exclude items shipping from overseas" reasons. The
  endpoint shape still needs verification against live Mercari before
  it ships.
- **Category / brand name resolution** — today the tool takes raw
  Mercari IDs. A `mercari_jp_category_lookup("iPhone cases")` that
  maps free-form strings to `categoryId` / `brandId` would let the
  LLM narrow on "only in スマホアクセサリー" without a hard-coded dict.
- **Skill plugin category** — [GOAL.md](GOAL.md) reserves a `skill`
  plugin category (subscribes to `user.message.received` with a
  filter, can emit any of the other kinds via the kernel). None are
  bundled yet. Moving slot extraction and retry policy into a first-
  class skill plugin would let the Mercari output contract pattern
  apply to other verticals (booking, research, code generation)
  without touching the strategy.
- **Authenticated Mercari sessions** — yaya never logs in today.
  Purchase, cart, and saved-search features would require an auth
  plugin and a capability-gated approval flow. Intentionally out of
  1.0 scope; slated for the 2.0 "marketplace + sandbox" milestone.
- **Self-authoring plugins (0.5 milestone)** — the kernel is ready,
  but the "describe a capability → yaya writes a plugin → installs it
  → the next turn uses it" loop is not wired end-to-end yet.
- **Multi-tool parallelism** — the agent loop is single-tool-per-turn.
  A "search 3 categories in parallel and merge" pattern would need
  either a new strategy plugin or loop changes.
- **Non-flaky compaction timing tests** —
  `tests/kernel/test_compaction.py` has two tests that occasionally
  miss their retry-count assertion on CI runners under load. Real
  bug, low severity, worth hunting down.

Open issues and milestones live at
[github.com/rararulab/yaya/issues](https://github.com/rararulab/yaya/issues).

## Further reading

Full site: **<https://rararulab.github.io/yaya/>**.

### User guide

- [docs/guide/install.md](docs/guide/install.md)
- [docs/guide/usage.md](docs/guide/usage.md)

### Development (contributors and coding agents)

- [AGENT.md](AGENT.md) — agent entry index.
- [docs/dev/architecture.md](docs/dev/architecture.md) — kernel + plugins layout.
- [docs/dev/plugin-protocol.md](docs/dev/plugin-protocol.md) — event catalog + plugin ABI (authoritative).
- [docs/dev/workflow.md](docs/dev/workflow.md) — issue → worktree → PR.
- [docs/dev/multi-agent.md](docs/dev/multi-agent.md)
- [docs/dev/cli.md](docs/dev/cli.md)
- [docs/dev/web-ui.md](docs/dev/web-ui.md)
- [docs/dev/testing.md](docs/dev/testing.md)
- [docs/dev/agent-spec.md](docs/dev/agent-spec.md)
- [docs/dev/release.md](docs/dev/release.md)

Org standards are inherited from
[`rararulab/.github`](https://github.com/rararulab/.github).

## License

MIT
