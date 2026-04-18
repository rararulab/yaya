## Philosophy
OpenAI LLM-provider plugin built on the official `openai.AsyncOpenAI` SDK (the only LLM SDK allowed by `AGENT.md` §4). Non-streaming chat completions at 0.1; streaming follows adapter work.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (LLM-provider row).
- Contract: [`specs/plugin-llm_openai.spec`](../../../../specs/plugin-llm_openai.spec).
- Tests: `tests/plugins/llm_openai/`.

## Constraints
- `Category.LLM_PROVIDER`. Subscribes only to `llm.call.request`; filters by `payload["provider"] == "openai"`.
- Env-driven config: `OPENAI_API_KEY` (required) + `OPENAI_BASE_URL` (optional). Missing key → `on_load` logs WARNING and sets `_configured = False`. No hard crash.
- Every response event echoes `request_id` (lesson #15). Applies to both `llm.call.response` and `llm.call.error`.
- `asyncio.CancelledError` propagates — never swallow it (lesson #3).
- `on_unload` calls `await self._client.close()` inside a try/except; cleanup errors log WARNING and never re-raise.
- No third-party AI agent frameworks (AGENT.md §4). `openai` SDK only.

## Interaction (patterns)
- Request filter: `ev.payload.get("provider") != "openai"` → return silently (sibling providers coexist on the same subscription).
- Not configured → emit `llm.call.error` with `{"error": "not_configured", "request_id": ev.id}`.
- `openai.RateLimitError` → `llm.call.error` with `retry_after_s` when the SDK exposes one.
- Any other `openai.APIError` / generic `Exception` → `llm.call.error` with `{"error": str(e), "request_id": ev.id}`.
- Do NOT emit `plugin.error` from this handler; the kernel synthesizes those when `on_event` raises.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#llm-invocation-kernel--llm-provider).
