spec: task
name: "plugin-web-instance-crud"
tags: [plugin, adapter, web, api, llm-provider, instance-crud]
---

## Intent

The bundled `web` adapter's `/api/llm-providers` surface migrates from
plugin-shaped to instance-shaped post-D4a/D4b. Provider instances now
live at `providers.<id>.{plugin, label, <fields>}` in the ConfigStore
and one backing plugin powers many instances (e.g. one `llm-openai`
plugin + three openai-flavored records). This PR ships full HTTP CRUD
over those instances so the browser UI (D4d) can add, edit, and
remove provider instances without a restart.

## Decisions

- **Instance-shaped list surface.** `GET /api/llm-providers` returns a
  bare JSON array of instance rows `{id, plugin, label, active,
  config, config_schema}`. `config_schema` is pulled from the backing
  plugin's `ConfigModel` when loaded; otherwise `None`. Secrets mask
  by suffix (`api_key` / `token` / `secret` / `password`); `?show=1`
  reveals.
- **Instance-id validator in the kernel.** `yaya.kernel.providers`
  grows `is_valid_instance_id(id)` — 3-64 chars, lowercase
  alphanumeric + dash, no dots, no leading/trailing dash. The rule
  excludes dots because `ProvidersView` splits `providers.<id>.<field>`
  on the first dot and an id with a dot would corrupt the grouping.
  This closes the #122 S2 follow-up.
- **Create semantics.** `POST /api/llm-providers` with
  `{id?, plugin, label?, config?}` materialises
  `providers.<id>.{plugin, label, <field>}` keys. When `id` is
  absent the handler generates `f"{plugin}-{uuid8}"`. Duplicate id
  → 409. Unknown (or non-llm-provider) plugin → 400. Writes are
  best-effort across multiple `ConfigStore.set` calls; a mid-way
  failure may leave a partial instance that the operator cleans up
  with `yaya config unset providers.<id>.*`.
- **Patch is a partial merge.** `PATCH /api/llm-providers/<id>`
  accepts `{label?, config?}`. Fields absent from the body are not
  written — existing `providers.<id>.<field>` values survive. The
  body model omits `plugin`: rebinding an instance is a
  delete+create, not a patch.
- **Delete has two safety 409s.** `DELETE /api/llm-providers/<id>`
  returns 409 when the target is the active instance ("switch
  active provider before deleting this one") and when it is the
  last instance of its backing plugin ("this is the only instance
  of <plugin>; add another before deleting"). The last-of-plugin
  check exists so an operator cannot accidentally strand their only
  credentials for a plugin.
- **Active switch migrates to instance ids.** `PATCH
  /api/llm-providers/active` still accepts `{name}` for backwards
  compatibility, but the value is now an instance id, not a plugin
  name. The handler validates the id resolves to an existing instance
  and that the backing plugin is currently loaded (else 400 — a
  dangling plugin reference would cause the strategy loop to
  dispatch into the void).
- **Test endpoint routes on a bridge session.** `POST
  /api/llm-providers/<id>/test` publishes one `llm.call.request`
  with `provider=<id>` on session id
  `_bridge:web-api-test:<uuid>` (lesson #2) so the probe never
  interleaves with a real conversation's tape. 404 when the id is
  unknown; 400 when the backing plugin is not loaded.
- **No frontend changes here.** D4d wires the UI against this
  surface. This PR keeps scope to Python + spec + docs.

## Boundaries

### Allowed Changes
- src/yaya/kernel/providers.py
- src/yaya/kernel/__init__.py
- src/yaya/plugins/web/api.py
- src/yaya/plugins/web/AGENT.md
- tests/plugins/web/test_web_config_api.py
- tests/kernel/test_providers_view.py
- specs/plugin-web-instance-crud.spec
- tests/bdd/features/plugin-web-instance-crud.feature
- docs/dev/plugin-protocol.md

### Forbidden
- src/yaya/plugins/web/src/ (D4d owns the frontend)
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/llm_echo/
- GOAL.md

## Completion Criteria

Scenario: Listing instances returns bare array with active flag
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_list_returns_shape
  Level: integration
  Given a config store seeded with two llm-openai instances and one llm-echo instance with provider set to openai-gpt4
  When a client issues GET api llm providers
  Then the response is a bare array with id plugin label active and config per row and openai-gpt4 is the only active row

Scenario: Listing instances masks secrets by default
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_list_masks_secrets
  Level: integration
  Given a config store seeded with an instance whose config carries an api_key field
  When a client issues GET api llm providers without the show flag
  Then the api_key value is masked in the response

Scenario: Show flag reveals secrets on list
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_list_show_reveals_secrets
  Level: integration
  Given a config store seeded with an instance whose config carries an api_key field
  When a client issues GET api llm providers with show equal to 1
  Then the api_key value is returned unmasked

Scenario: Creating an instance materialises providers keys and returns 201
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_create_happy_path
  Level: integration
  Given an admin router wired to a stub registry with llm-openai loaded
  When a client POSTs api llm providers with id plugin label and config
  Then the response is 201 and providers id plugin providers id label and providers id config fields are written
  And the returned row echoes the supplied values

Scenario: Creating an instance with a dotted id is rejected
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_create_rejects_invalid_id
  Level: integration
  Given an admin router wired to a stub registry
  When a client POSTs api llm providers with an id containing a dot
  Then the response is 400 with a message explaining the id must not contain dots

Scenario: Creating an instance with a duplicate id returns 409
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_create_rejects_duplicate_id
  Level: integration
  Given a config store seeded with an existing llm-openai instance
  When a client POSTs api llm providers with the same id
  Then the response is 409

Scenario: Creating an instance with an unknown plugin returns 400
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_create_rejects_unknown_plugin
  Level: integration
  Given an admin router wired to a registry without llm-missing loaded
  When a client POSTs api llm providers referencing a non-llm-provider plugin
  Then the response is 400

Scenario: Creating an instance without id auto-generates one
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_create_auto_generates_id_when_absent
  Level: integration
  Given an admin router wired to a stub registry with llm-openai loaded
  When a client POSTs api llm providers omitting id
  Then the returned row carries an id of the form plugin dash uuid8 and the label falls back to plugin id

Scenario: Patching an instance merges label and config partially
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_patch_merges_config_partial
  Level: integration
  Given a config store seeded with an instance that has an existing api_key
  When a client PATCHes api llm providers id with a new label and a new model
  Then the label and model are updated and the api_key field survives untouched

Scenario: Patching on an unknown instance id returns 404
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_patch_on_unknown_id
  Level: integration
  Given an admin router wired to a stub registry
  When a client PATCHes api llm providers ghost with a label
  Then the response is 404

Scenario: Deleting an instance clears its providers keys
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_delete_happy_path
  Level: integration
  Given a config store seeded with two llm-openai instances
  When a client DELETEs api llm providers openai-gpt4
  Then the response is 204 and providers openai-gpt4 plugin and providers openai-gpt4 api_key are removed

Scenario: Deleting the active instance is blocked with 409
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_delete_rejects_active
  Level: integration
  Given a config store where provider equals openai-gpt4 and that instance exists
  When a client DELETEs api llm providers openai-gpt4
  Then the response is 409 with a message about switching the active provider

Scenario: Deleting the last instance of a plugin is blocked with 409
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_delete_rejects_last_of_plugin
  Level: integration
  Given a config store where llm-echo has exactly one instance
  When a client DELETEs that instance
  Then the response is 409 with a message about being the only instance

Scenario: Switching active to an unknown instance id returns 404
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_active_switch_validates_instance_id
  Level: integration
  Given a config store seeded with known instances
  When a client PATCHes api llm providers active with an unknown id
  Then the response is 404

Scenario: Switching active to an instance whose plugin is not loaded returns 400
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_active_switch_validates_plugin_loaded
  Level: integration
  Given a config store seeded with an instance whose plugin is not loaded
  When a client PATCHes api llm providers active with that id
  Then the response is 400

Scenario: Test endpoint routes on a bridge session
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_config_api.py::test_instance_test_endpoint_routes_on_bridge_session
  Level: integration
  Given an admin router wired to a bus whose stub provider echoes llm call response for the instance id
  When a client POSTs api llm providers id test
  Then the llm call request carries a bridge web-api-test session id and the response is ok true with latency_ms

## Out of Scope

- Frontend settings UI wiring against the new instance surface — D4d.
- Authentication and authorization — GOAL.md non-goals through 1.0.
- A batch / atomic ConfigStore write primitive — partial-instance
  cleanup stays operator-driven for now.
- Garbage-collecting legacy `plugin.<ns>.*` rows once every bundled
  `llm-provider` reads instance-scoped config — tracked separately.
