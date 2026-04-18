## Philosophy
Echo LLM-provider plugin — deterministic, zero-config, dev-only. Ships so a fresh `yaya serve` round-trips the kernel end-to-end without any API key. Closes the 0.1 onboarding gap (`GOAL.md` §Milestones 0.1).

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (LLM-provider row).
- Contract: [`specs/plugin-llm_echo.spec`](../../../../specs/plugin-llm_echo.spec).
- Tests: `tests/plugins/llm_echo/`.

## Constraints
- `Category.LLM_PROVIDER`. Subscribes only to `llm.call.request`; filters by `payload["provider"] == "echo"`.
- Stdlib only. No third-party AI agent frameworks (`AGENT.md` §4); no LLM SDK.
- Deterministic: `(echo) <last user message>` for non-empty user input, else the literal `(echo) (no input)`.
- Every `llm.call.response` echoes `request_id` (lesson #15).
- `on_unload` is a no-op — the plugin holds no resources.

## Interaction (patterns)
- Request filter: `ev.payload.get("provider") != "echo"` → return silently (sibling providers coexist).
- Iterate `messages` in reverse to grab the most recent `role == "user"` `content` string.
- Token usage hard-coded to `{"input_tokens": 0, "output_tokens": 0}` — this is a dev provider, not an LLM call.
- Auto-selection lives in the strategy (see `strategy_react/plugin.py`): when `OPENAI_API_KEY` is unset, the strategy picks `provider == "echo"`. Temporary env sniff; migrates to `ctx.config` in #23.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#llm-invocation-kernel--llm-provider).
