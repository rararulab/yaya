## Philosophy
Echo LLM-provider plugin — deterministic, zero-config, dev-only. Ships so a fresh `yaya serve` round-trips the kernel end-to-end without any API key. Closes the 0.1 onboarding gap (`GOAL.md` §Milestones 0.1). **Instance-scoped** (D4b / #123): filters by the set of owned instance ids under `providers.<id>.*`, matching the D4a-seeded `llm-echo` instance by default.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (LLM-provider row + "Provider instances" section).
- Contracts: [`specs/plugin-llm_echo.spec`](../../../../specs/plugin-llm_echo.spec) + [`specs/instance-dispatch.spec`](../../../../specs/instance-dispatch.spec).
- Tests: `tests/plugins/llm_echo/`.

## Constraints
- `Category.LLM_PROVIDER`. Subscribes to `llm.call.request` and `config.updated`; filters `payload["provider"]` against `self._active_instances`.
- Stdlib only. No third-party AI agent frameworks (`AGENT.md` §4); no LLM SDK.
- Deterministic: `(echo) <last user message>` for non-empty user input, else the literal `(echo) (no input)`.
- Every `llm.call.response` echoes `request_id` (lesson #15).
- `on_unload` clears the active-instance set — the plugin holds no other resources.

## Interaction (patterns)
- Request filter: `ev.payload.get("provider") not in self._active_instances` → return silently.
- `config.updated` under `providers.*` → re-snapshot the owned set via `ctx.providers.instances_for_plugin(self.name)`; no fine-grained diff.
- Iterate `messages` in reverse to grab the most recent `role == "user"` `content` string.
- Token usage hard-coded to `{"input_tokens": 0, "output_tokens": 0}` — dev provider, not an LLM call.
- Active-instance selection lives in `strategy_react`: when no `OPENAI_API_KEY` and `ctx.providers` is absent, the strategy falls back to `provider == "llm-echo"` (matches the D4a-seeded instance id).

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#llm-invocation-kernel--llm-provider).
