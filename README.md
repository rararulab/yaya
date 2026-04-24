# yaya

[![CI](https://github.com/rararulab/yaya/actions/workflows/main.yml/badge.svg)](https://github.com/rararulab/yaya/actions/workflows/main.yml)

A local-first agent that recommends Mercari Japan products from a
natural-language ask. `yaya serve` boots an event-driven kernel, opens
a chat in your browser, and answers queries like *"find me 3 iPhone 15
cases under ¥1000, new condition"* via the bundled `mercari_jp_search`
tool.

## Overview

Three pieces, all small:

- **Kernel** — async event bus + plugin registry + a fixed ReAct agent
  loop. Knows nothing about Mercari or any specific LLM.
- **Plugins** — everything user-visible. Bundled: `web` adapter,
  `llm_openai` provider, `mercari_jp` tool, `strategy_react` strategy,
  `memory_sqlite` store.
- **Agent loop** — ReAct cycle. Strategy parses `Thought / Action /
  Action Input`, kernel dispatches the tool call, result comes back
  as `Observation:` for the next LLM turn.

One Mercari turn:

```
user → strategy_react → llm_openai → mercari_jp_search (api.mercari.jp)
                                  ↓
                              20 candidates
                                  ↓
                     llm_openai → 3-row markdown table → browser
```

Architecture details: [docs/dev/architecture.md](docs/dev/architecture.md).

## Setup

Python 3.14+.

```bash
# from source (recommended for reviewers)
git clone https://github.com/rararulab/yaya.git
cd yaya
uv sync
uv run yaya version

# or a release wheel
pip install "yaya @ https://github.com/rararulab/yaya/releases/latest/download/yaya-py3-none-any.whl"
```

Configure an LLM provider (bundled `llm_echo` works offline for a
smoke test):

```bash
# OpenAI-native
export OPENAI_API_KEY=sk-...

# or any OpenAI-compatible endpoint
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

Opens `http://127.0.0.1:<port>/` in your browser. Example prompt:

> *Find me 3 iPhone 15 cases on Mercari JP under ¥1000, new condition, seller pays shipping.*

Output is a strict 3-row markdown table, each `Why it fits` cell
citing a constraint the user actually stated:

| Rank | Title | Price (JPY) | Condition | Why it fits | Link |
|------|-------|-------------|-----------|-------------|------|
| 1 | iPhone 15 Pro clear TPU | ¥380 | 新品、未使用 | under ¥1000, new, seller pays shipping | https://jp.mercari.com/item/… |

More commands: `yaya doctor`, `yaya plugin list`, `yaya --json doctor`.

## Design Choices

- **Kernel-style, not agent framework.** No LangChain / LlamaIndex /
  AutoGen — they ship orchestration opinions you cannot undo. yaya's
  kernel is event-bus + registry + fixed loop; everything else is a
  plugin. LLM access is limited to the official `openai` / `anthropic`
  SDKs ([AGENT.md §4](AGENT.md#4-constraints)).
- **Mercapi, not scraping.** `mercari_jp_search` signs a DPoP JSON
  request to the same endpoint the Mercari mobile app uses — no
  Playwright, no HTML parsing, no CAPTCHA. 403 / anti-bot responses
  are terminal (`ToolError(kind="rejected")`), never bypassed.
- **Filter-rich tool surface.** The tool exposes Mercari's native
  `category_ids` / `brand_ids` / `item_condition` / `shipping_payer`
  alongside keyword and price. The LLM picks them from the user's
  phrasing ("new", "under ¥1000", "送料込み").
- **ReAct + pinned output contract.** The `strategy_react` system
  prompt has two stacked contracts: (1) `Thought:` is an internal
  scratchpad, `Final Answer:` is the only user-visible surface; (2)
  when `mercari_jp_search` is registered, the prompt hard-pins the
  final answer to the 3-row table shape above, with non-generic
  `Why it fits` cells and no duplicate reasons.
- **Typed tool envelope.** Tools return `ToolOk(brief, display)` /
  `ToolError(kind, brief, display)`. The agent loop projects the
  envelope into a parseable `Observation:`; the UI renders each call
  as a collapsible card so the transcript stays clean.

## Potential Improvements

- `mercari_jp_item_detail(item_id)` — search has no seller rating,
  full description, or ship-from region. Deferred until the endpoint
  shape is verified against live Mercari.
- `mercari_jp_category_lookup` — free-form string → `categoryId` /
  `brandId`, so the LLM can narrow without hand-coded IDs.
- **Skill plugin** — the output contract is prompt-only today;
  promoting it to a first-class `skill` plugin would let the same
  pattern apply to other verticals (research, booking, code gen).
- **Authenticated sessions** — cart / purchase / saved-search needs an
  auth plugin and a capability-gated approval flow. Intentionally out
  of 1.0 scope.
- **Self-authoring plugins** — the kernel is ready; the "describe a
  capability → yaya writes, installs, and uses a new plugin" loop is
  not end-to-end yet (0.5 milestone).
- **Multi-tool parallelism** — the loop is single-tool-per-turn;
  parallel fan-out needs a new strategy.

## Further reading

- [docs/guide/install.md](docs/guide/install.md) · [docs/guide/usage.md](docs/guide/usage.md)
- [docs/dev/architecture.md](docs/dev/architecture.md) — kernel layout
- [docs/dev/plugin-protocol.md](docs/dev/plugin-protocol.md) — event catalog + plugin ABI
- [GOAL.md](GOAL.md) · [AGENT.md](AGENT.md)

## License

MIT
