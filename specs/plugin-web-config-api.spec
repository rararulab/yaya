spec: task
name: "plugin-web-config-api"
tags: [plugin, adapter, web, api, config]
---

> **Superseded note (D4c).** Instance-shaped CRUD for LLM providers
> moved to `specs/plugin-web-instance-crud.spec` in D4c; the
> active-switch + test-connection ACs in this spec cover the pre-D4c
> generic wiring and the post-D4c instance-aware behaviour jointly —
> the two scenarios at the bottom of Completion Criteria bind to the
> D4c replacement tests rather than to the pre-D4c plugin-shaped ones.

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
- src/yaya/plugins/web/src/chat-shell.ts
- src/yaya/plugins/web/src/types.ts
- src/yaya/plugins/web/src/__tests__/chat-shell.test.ts
- src/yaya/plugins/web/static/
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/config_store.py
- src/yaya/kernel/session.py
- src/yaya/plugins/llm_openai/plugin.py
- tests/plugins/web/test_web_config_api.py
- tests/plugins/web/test_web_sessions_api.py
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
    Filter: tests/plugins/web/test_web_config_api.py::test_active_switch_happy_path
  Level: integration
  Given an admin router wired to a config store seeded with llm provider instances
  When a client PATCHes api llm providers active with an instance id
  Then the store records provider equal to that instance id and the response is the refreshed list

Scenario: LLM provider test endpoint round-trips through the bus
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_test_endpoint_routes_on_bridge_session
  Level: integration
  Given an admin router wired to a bus whose stub provider echoes llm call response for an instance id
  When a client POSTs api llm providers id test
  Then the llm call request carries a bridge web-api-test session id and the response is ok true with latency_ms

Scenario: llm_openai hot reload preserves in flight calls
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_llm_openai_hot_reload_preserves_in_flight_call
  Level: integration
  Given a running llm openai plugin with a live client
  When a config updated event triggers a client rebuild
  Then the previous client is never closed and a fresh client is built

Scenario: GET api sessions lists persisted tapes for the current workspace
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_sessions_list_returns_persisted_tape
  Level: unit
  Given an admin router wired to a live SessionStore with one appended user message
  When a client GETs api sessions
  Then the response lists one row with id entry_count and tape_name populated

Scenario: GET api sessions returns 503 when the adapter has no session store wired
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_sessions_list_503_when_no_store
  Level: unit
  Given an admin router with session_store None and workspace None
  When a client GETs api sessions
  Then the response status is 503

Scenario: Session list rows include a preview sourced from the first user message
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_sessions_list_row_includes_user_message_preview
  Level: unit
  Given a SessionStore with a tape whose first message is from a user
  When a client GETs api sessions
  Then the row carries a preview field equal to the user message content

Scenario: GET api sessions id messages returns the projected history
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_messages_endpoint_returns_projected_history
  Level: unit
  Given a SessionStore with a tape carrying alternating user and assistant messages
  When a client GETs api sessions id messages for that tape
  Then the response messages list mirrors the loop projection in tape order

Scenario: Messages endpoint elides history before a compaction anchor
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_messages_endpoint_elides_history_before_compaction_anchor
  Level: unit
  Given a SessionStore with a tape whose entries straddle a compaction anchor
  When a client GETs api sessions id messages for that tape
  Then the response replaces pre compaction entries with a system summary row

Scenario: Messages endpoint 404s when the session id is unknown
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_messages_endpoint_404_when_id_unknown
  Level: unit
  Given an admin router wired to an empty SessionStore
  When a client GETs api sessions id messages for a missing id
  Then the response status is 404

Scenario: GET api sessions id frames returns live shape frames for UI replay
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_frames_endpoint_returns_live_shape_frames
  Level: unit
  Given a SessionStore with a tape carrying a user message a tool call a tool result and an assistant message
  When a client GETs api sessions id frames for that tape
  Then the response frames list emits user.message tool.start tool.result and assistant.done in tape order

Scenario: Frames endpoint 404s when the session id is unknown
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_frames_endpoint_404_when_id_unknown
  Level: unit
  Given an admin router wired to an empty SessionStore
  When a client GETs api sessions id frames for a missing id
  Then the response status is 404

Scenario: Messages endpoint 503s when no session store is wired
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_messages_endpoint_503_when_no_store
  Level: unit
  Given an admin router with session_store None and workspace None
  When a client GETs api sessions id messages for any id
  Then the response status is 503

Scenario: DELETE api sessions id archives the tape and drops it from the list
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_delete_session_archives_the_tape
  Level: unit
  Given a SessionStore with one persisted tape carrying a user message
  When a client DELETEs api sessions id for that tape
  Then the response status is 204 and the follow up list omits the row

Scenario: DELETE api sessions id returns 404 when the id is unknown
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_delete_session_404_when_id_unknown
  Level: unit
  Given an admin router wired to an empty SessionStore
  When a client DELETEs api sessions id for an unknown id
  Then the response status is 404

Scenario: DELETE api sessions id returns 503 when no session store is wired
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_delete_session_503_when_no_store
  Level: unit
  Given an admin router with session_store None and workspace None
  When a client DELETEs api sessions id for any id
  Then the response status is 503

Scenario: PATCH api sessions id writes a name surfaced on subsequent list
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_patch_session_writes_name_and_is_reflected_in_list
  Level: unit
  Given a SessionStore with one persisted tape carrying a user message
  When a client PATCHes api sessions id with a name body
  Then the response row and the follow up list both carry the new name

Scenario: PATCH api sessions id returns 404 when the id is unknown
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_patch_session_404_when_id_unknown
  Level: unit
  Given an admin router wired to an empty SessionStore
  When a client PATCHes api sessions id with a name body for an unknown id
  Then the response status is 404

Scenario: PATCH api sessions id returns 400 when the name is blank
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_sessions_api.py::test_patch_session_400_when_name_empty
  Level: unit
  Given a SessionStore with one persisted tape carrying a user message
  When a client PATCHes api sessions id with a whitespace only name
  Then the response status is 400

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
