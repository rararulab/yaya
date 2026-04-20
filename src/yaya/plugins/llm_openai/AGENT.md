## Philosophy
OpenAI LLM-provider plugin built on the official `openai.AsyncOpenAI` SDK (the only LLM SDK allowed by `AGENT.md` §4). **Instance-scoped** (D4b / #123): one plugin process backs many operator-configured records under `providers.<id>.*` — "OpenAI prod", "Azure OpenAI", "local-LM-Studio" each with its own `api_key`, `base_url`, `model`. Non-streaming chat completions at 0.1; streaming follows adapter work.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (LLM-provider row + "Provider instances" section).
- Contracts: [`specs/plugin-llm_openai.spec`](../../../../specs/plugin-llm_openai.spec) + [`specs/instance-dispatch.spec`](../../../../specs/instance-dispatch.spec).
- Tests: `tests/plugins/llm_openai/` + `tests/kernel/test_strategy_hot_provider.py::test_llm_openai_rebuilds_client_on_config_updated`.

## Constraints
- `Category.LLM_PROVIDER`. Subscribes to `llm.call.request` and `config.updated`; filters `payload["provider"]` against `self._clients` keyed by instance id.
- Per-instance config: `providers.<id>.api_key` wins over `OPENAI_API_KEY`; `providers.<id>.base_url` overrides `OPENAI_BASE_URL`. Instances with neither key nor env fallback log a WARNING at load and are absent from `self._clients` — the bus surfaces "no subscriber" silence for them.
- Every response event echoes `request_id` (lesson #15). Applies to both `llm.call.response` and `llm.call.error`.
- `asyncio.CancelledError` propagates — never swallow it (lesson #3).
- `on_unload` drops `self._clients` without awaiting the old clients' `close()` — pool reclamation rides GC so in-flight dispatches finish cleanly (preserves the #106 fix).
- No third-party AI agent frameworks (AGENT.md §4). `openai` SDK only.

## Interaction (patterns)
- Request filter: `ev.payload.get("provider") not in self._clients` → return silently.
- `config.updated` key under `providers.<id>.*`: look up the instance; absent or no longer owned → drop client; otherwise rebuild just that instance's client. Unrelated prefixes ignored.
- `openai.RateLimitError` → `llm.call.error` with `retry_after_s` when the SDK exposes one.
- Any other `openai.APIError` / generic `Exception` → `llm.call.error` with `{"error": str(e), "request_id": ev.id}`.
- Do NOT emit `plugin.error` from this handler; the kernel synthesizes those when `on_event` raises.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#llm-invocation-kernel--llm-provider).
