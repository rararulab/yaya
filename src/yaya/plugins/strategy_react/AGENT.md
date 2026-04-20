## Philosophy
ReAct strategy plugin — observe → think → act. Drives `strategy.decide.request` → `strategy.decide.response` with `next` ∈ `{llm, tool, done}`. The loop owns ordering; this plugin picks content only.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (Agent loop + Strategy rows).
- Contract: [`specs/plugin-strategy-react.spec`](../../../../specs/plugin-strategy-react.spec).
- Tests: `tests/plugins/strategy_react/`.

## Constraints
- `Category.STRATEGY`. Subscribes only to `strategy.decide.request`.
- **Echo `request_id`** on every response (lesson #15). The loop's `_RequestTracker` drops uncorrelated events with a WARNING.
- Provider + model resolution (D4b / #123): the `(provider, model)` pair comes from `ctx.providers.active_id` → `ctx.providers.get_instance(active_id).config["model"]`. Fallback (no providers view): env sniff over `OPENAI_API_KEY` → `llm-openai`/`gpt-4o-mini`, else `llm-echo`/`echo`. Fallback instance names mirror the D4a-seeded ids verbatim.
- Pure-function core (`_decide`): no bus, no state — trivially unit-testable.
- No third-party AI agent frameworks (AGENT.md §4). Stdlib + `yaya.kernel.*` only.

## Interaction (patterns)
- Next-step rules (evaluated top-down):
  1. Most-recent assistant msg carries `tool_calls` → `{"next": "tool", "tool_call": <first>}`.
  2. Assistant msg exists without tool_calls, no pending tool result → `{"next": "done"}`.
  3. Assistant just consumed a tool result → `{"next": "llm", provider, model}` for another pass.
  4. No assistant msg yet → `{"next": "llm", provider, model}`.
- Do NOT emit `memory.*` requests from this strategy at 0.1 — memory is out of scope for the seed ReAct.
- Do NOT keep per-session state here; the loop's `state` snapshot is the source of truth.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#agent-loop-kernel-owned).
