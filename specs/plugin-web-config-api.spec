spec: task
name: "plugin-web-config-api"
tags: [plugin, adapter, web, api, config]
---

## Intent

The bundled `web` adapter gains an HTTP admin API on top of the
kernel's live `ConfigStore` and `PluginRegistry` so the browser UI
can drive config edits, plugin enable / install / remove, and LLM-
provider selection without a kernel restart. The API is the browser
UI's control plane; the UI (PR C in the current stack) binds form
widgets to these endpoints. The API is unauthenticated — `yaya
serve` binds `127.0.0.1` only through 1.0, so local-only is the sole
authorization.

## Decisions

- **Router factored into `api.py`.** `build_admin_router(registry,
  config_store, bus)` returns a `fastapi.APIRouter` the adapter
  mounts inside `_build_app`. Tests exercise the router directly
  without standing up uvicorn.
- **Kernel-side escape hatch.** `KernelContext` grows `.registry`
  and `.config_store` properties (same pattern as `.bus` and
  `.session`). The admin router reads these from `self._ctx` at
  `_build_app` time. When either is `None` — tests wiring the ASGI
  app without a live registry — the affected endpoints return 503,
  and the pre-API cached `/api/plugins` fallback is registered
  first so legacy tests keep passing.
- **Secret masking by suffix.** Keys whose last dotted segment is
  one of `api_key`, `token`, `secret`, `password` mask to
  `****<last4>` (strings) or `****` (non-strings / short strings).
  `GET /api/config` masks; `GET /api/config/{key}` masks; `?show=1`
  reveals. The mask set mirrors `yaya config list` (the CLI and the
  API must redact the same things).
- **Plugin `enabled` is reload-gated.** `PATCH /api/plugins/<name>`
  writes `plugin.<ns>.enabled` through ConfigStore and returns
  `{reload_required: true}`. No kernel-level runtime enable/disable
  primitive — that is a larger refactor. The UI surfaces the flag.
- **Install validation.** `POST /api/plugins/install` forwards the
  source string through the registry's existing
  `validate_install_source` before calling `registry.install`.
  Disallowed characters / schemes surface as 400 so the API does
  not leak pip errors.
- **LLM-provider switch.** `PATCH /api/llm-providers/active` writes
  config key `provider`. `strategy_react` reads `provider` at each
  turn, so the switch takes effect on the next `llm.call.request`
  without a reload. The target must be a loaded `llm-provider`
  plugin; mismatch → 400, unknown plugin → 404.
- **`/test` round-trip.** `POST /api/llm-providers/<name>/test`
  publishes one `llm.call.request` with a fresh session id and a
  trivial prompt (`say OK`), subscribes to both
  `llm.call.response` and `llm.call.error`, and waits up to 5 s.
  Returns `{ok, latency_ms, error?}`. The endpoint is reused by
  the UI's "test connection" button.
- **127.0.0.1-only binding unchanged.** The adapter still hard-codes
  `_BIND_HOST = "127.0.0.1"`; adding admin endpoints does not change
  the bind posture. The unauthenticated surface is documented in
  `src/yaya/plugins/web/AGENT.md`.
- **PR #105 follow-up polish (same PR).** Drops `await
  existing.close()` in `llm_openai` rebuild (F1: preserves
  in-flight `chat.completions.create`); extracts `_scoped_keys`
  helper on `ConfigView` (S1); upgrades the silent
  `TypeError` skip during TOML migration to a `WARN` log (S2).

## Boundaries

### Allowed Changes
- src/yaya/plugins/web/api.py
- src/yaya/plugins/web/plugin.py
- src/yaya/plugins/web/AGENT.md
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/config_store.py
- src/yaya/plugins/llm_openai/plugin.py
- tests/plugins/web/test_web_config_api.py
- tests/plugins/llm_openai/test_llm_openai.py
- specs/plugin-web-config-api.spec
- tests/bdd/features/plugin-web-config-api.feature
- docs/dev/plugin-protocol.md
- docs/dev/web-ui.md

### Forbidden
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/tool_bash/
- GOAL.md

## Completion Criteria

Scenario: Config CRUD round-trips through the API
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_config_crud_cycle
  Level: integration
  Given an admin router wired to a live config store
  When a client issues a PATCH then GET then DELETE for a config key
  Then each response reflects the new state and the final GET returns 404

Scenario: Secret-suffix keys mask unless show flag is set
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_config_secret_masking_honours_show_flag
  Level: integration
  Given an admin router wired to a live config store containing a secret key
  When a client reads the key with and without the show flag
  Then the default response is masked and the show flag reveals the value

Scenario: Plugin list exposes enabled flag and current config
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_plugins_list_exposes_metadata
  Level: integration
  Given an admin router wired to a stub registry and a live store
  When a client issues GET api plugins
  Then the response includes enabled status schema and current_config per plugin

Scenario: Patching enabled writes the plugin enabled config key
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_plugin_patch_writes_enabled_flag
  Level: integration
  Given an admin router wired to a stub registry and a live store
  When a client PATCHes api plugins with enabled false
  Then the store records plugin ns enabled false and the response signals reload_required

Scenario: Plugin install delegates to the registry
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_plugin_install_delegates_to_registry
  Level: integration
  Given an admin router wired to a stub registry
  When a client POSTs api plugins install with a valid source
  Then the stub registry records the install call and the response is ok

Scenario: Switching the active LLM provider writes the provider config key
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_llm_provider_active_switch
  Level: integration
  Given an admin router wired to a stub registry with two LLM provider plugins
  When a client PATCHes api llm providers active with one provider name
  Then the store records provider equal to that name and the response is ok

Scenario: LLM provider test endpoint round-trips through the bus
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_llm_provider_test_roundtrip
  Level: integration
  Given an admin router wired to a bus where a stub provider echoes llm call response
  When a client POSTs api llm providers name test
  Then the response carries ok true and a non negative latency_ms

Scenario: llm_openai hot reload preserves in flight calls
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_llm_openai_hot_reload_preserves_in_flight_call
  Level: integration
  Given a running llm openai plugin with a live client
  When a config updated event triggers a client rebuild
  Then the previous client is never closed and a fresh client is built

## Out of Scope

- Authentication, authorization, and public-bind support — GOAL.md
  non-goals through 1.0.
- Rate limiting — future PR; the local-only bind bounds the blast
  radius today.
- Runtime plugin enable / disable without reload — larger refactor
  of the bus's subscription gating layer.
- Plugin config editing at arbitrary dotted paths beyond the flat
  `plugin.<ns>.*` namespace — the UI reads the JSON Schema and
  writes keys as-is; nested sub-tree editing is left to the UI.
