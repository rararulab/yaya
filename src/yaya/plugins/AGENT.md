## Philosophy
Bundled-plugin home. Each subpackage is one plugin that loads through the **same** entry-point ABI as third-party plugins (`yaya.plugins.v1`). Seed set proves the protocol end-to-end: one plugin per non-adapter category.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../docs/dev/plugin-protocol.md) is the authoritative ABI + event contract.
- Seed plugins verified by `tests/plugins/<name>/` and `specs/plugin-<name>.spec` (BDD, `agent-spec lint` 0 WARN).
- `pyproject.toml` `[project.entry-points."yaya.plugins.v1"]` registers bundled plugins; discovery runs through the kernel registry like any third-party package.

## Constraints
- **Layering.** Plugins import only from `yaya.kernel.*` + stdlib + the library their category sanctions (`openai` for `llm_openai`, `sqlite3` for `memory_sqlite`). **Plugins never import from other plugins, from `yaya.cli.*`, or from `yaya.core.*`.** Cross-plugin communication happens exclusively via events on the bus.
- **Subscription discipline.** Only subscribe to the kinds your category routes for (protocol-doc table). Filter by payload inside the handler — by `provider` for `llm-provider`, by `name` for `tool`. No wildcards at 1.0.
- **Correlation.** Every response event echoes `request_id` from the originating request (see lesson #15 in `docs/wiki/lessons-learned.md`). Missing it hangs the loop until `step_timeout_s`.
- **Seed set.**
  - `strategy_react/` — `Category.STRATEGY` · subscribes to `strategy.decide.request` · emits `strategy.decide.response`.
  - `memory_sqlite/` — `Category.MEMORY` · subscribes to `memory.query` + `memory.write` · emits `memory.result`.
  - `llm_openai/` — `Category.LLM_PROVIDER` · subscribes to `llm.call.request` · emits `llm.call.response` or `llm.call.error`.
  - `tool_bash/` — `Category.TOOL` · subscribes to `tool.call.request` · emits `tool.call.result`.
- Each plugin has its own folder `AGENT.md` covering its Constraints + Interaction contract.

## Interaction (patterns)
- Add a plugin: new subpackage with `__init__.py` exposing `plugin: Plugin`, folder `AGENT.md`, `specs/plugin-<name>.spec`, tests under `tests/plugins/<name>/`, and an entry-point line in `pyproject.toml`.
- Never emit `plugin.error` or `kernel.error` directly — only the kernel synthesizes them. Raise from `on_event` instead.
- Do NOT spawn background tasks that publish out of order for the same session — FIFO per session is the bus's contract and plugins must not subvert it.
- Do NOT use `shell=True` in any subprocess primitive; always argv form.

## Budget & Loading
- [`docs/dev/plugin-protocol.md`](../../../docs/dev/plugin-protocol.md) · [`docs/wiki/lessons-learned.md`](../../../docs/wiki/lessons-learned.md) (especially #1, #3, #15, #18).
- Sibling indexes: [`../kernel/AGENT.md`](../kernel/AGENT.md) · [`../../../tests/AGENT.md`](../../../tests/AGENT.md).
