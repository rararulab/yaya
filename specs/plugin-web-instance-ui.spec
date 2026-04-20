spec: task
name: "plugin-web-instance-ui"
tags: [plugin, web, ui, llm-provider, instance-ui]
---

## Intent

The bundled `web` adapter's Settings → LLM Providers tab migrates from
the pre-D4 plugin-centric view to an instance-centric view. Each row
in the tab corresponds to one `providers.<id>.*` instance exposed by
the D4c HTTP CRUD surface (`GET/POST/PATCH/DELETE /api/llm-providers`,
`PATCH /api/llm-providers/active`, `POST /api/llm-providers/<id>/test`).
Operators switch the active instance with a radio, rename the label
inline, edit the config with a schema-driven form, probe the
connection, and delete — all without leaving the browser. A
`+ Add instance` affordance pops a form that picks the backing plugin
and seeds initial config fields. This PR completes the multi-instance
provider feature (D4a namespace → D4b plugin dispatch → D4c HTTP CRUD
→ D4d UI).

## Decisions

- **Row shape.** One row per instance, keyed on the instance id.
  Radio (active selector) + label (editable inline) + backing plugin
  name + status dot (connected / failed / untested) + expand/collapse
  toggle. Clicking an inactive radio fires
  `PATCH /api/llm-providers/active` with `{name: <id>}` (backwards
  compatible body shape — the value is an instance id post-D4c).
- **Schema-driven config form.** Expanding a row renders the backing
  plugin's JSON Schema via the existing `schema-form.ts`
  (secret-suffix heuristic handles password reveal). Save sends a
  PATCH with only the diffed fields against the server row; Reset
  reverts to server state; Delete opens a confirmation modal before
  firing `DELETE /api/llm-providers/<id>`. 4xx responses render as
  inline row errors (not banner) so the operator sees the actionable
  message next to the row it applies to.
- **Test connection per row.** A dedicated button fires
  `POST /api/llm-providers/<id>/test`, shows a spinner label while
  awaiting, and records the `{ok, latency_ms, error?}` result in a
  local `testResults` map keyed on id. The status dot reads from
  that map: green on ok, red on failure, grey when untested.
- **Add instance modal.** `+ Add instance` opens a modal-in-modal
  form: plugin dropdown (filtered to `category === "llm-provider"`
  from `/api/plugins`), instance id input (auto-suggested from the
  plugin name + counter), optional label, plus the backing plugin's
  schema form to seed initial config. Client-side id validation uses
  `isValidInstanceId` (mirrors `yaya.kernel.providers.is_valid_instance_id`).
  Submit → `POST /api/llm-providers`; 400/409 responses render
  inline inside the modal. Success closes the modal, re-fetches the
  list, and expands the new row.
- **API client surface.** `src/api.ts` grows
  `createLlmProvider`, `updateLlmProvider`, `deleteLlmProvider`, and
  `isValidInstanceId`. `listLlmProviders` accepts an optional
  `show=true` flag for the reveal-secret fetch. `ApiError` carries
  the parsed `detail` string so the UI can surface the server's
  actionable message instead of a generic status line.
- **Backward compat.** Pre-existing tab surfaces (Plugins, Advanced)
  are untouched. The bootstrap default instance renders as one row
  with the default label — no empty state required.
- **Bundle budget.** Kept under the ≤80 KiB gzipped entry + ≤10 KiB
  gzipped settings chunk limits. The instance tab adds no new
  runtime dependencies — reuses `schema-form.ts`, the existing
  modal pattern, and the `LitElement` shell.
- **Mirror feature.** The `.feature` mirror ships with matching
  scenarios; each scenario binds to a real vitest node id (see
  `src/__tests__/settings-view-instances.test.ts`).

## Boundaries

### Allowed Changes
- src/yaya/plugins/web/src/api.ts
- src/yaya/plugins/web/src/settings-view.ts
- src/yaya/plugins/web/src/app.css
- src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts
- src/yaya/plugins/web/static/
- src/yaya/plugins/web/static/**
- src/yaya/plugins/web/AGENT.md
- specs/plugin-web-instance-ui.spec
- tests/bdd/features/plugin-web-instance-ui.feature
- tests/bdd/test_plugin_web_instance_ui.py
- docs/dev/web-ui.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/web/api.py
- src/yaya/plugins/web/plugin.py
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/llm_echo/
- src/yaya/plugins/strategy_react/
- GOAL.md

## Completion Criteria

Scenario: LLM Providers tab renders one row per instance with active radio set
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::renders one row per instance with active radio set
  Level: unit
  Given the provider list is seeded with three instances and one active
  When the settings view mounts
  Then one row per instance is rendered and the active radio matches the seeded id

Scenario: Clicking a non-active radio PATCHes active with the instance id
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::clicking a non-active radio fires PATCH /active with the instance id
  Level: unit
  Given the provider list has an inactive row for llm-openai-2
  When the operator clicks the inactive radio
  Then a PATCH to api llm providers active fires with name equal to llm-openai-2

Scenario: Expanding a row renders the schema-driven form with action buttons
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::expanding a row renders the schema-driven form + label + action buttons
  Level: unit
  Given a row whose backing plugin exposes a config schema
  When the operator expands the row
  Then the schema fields render inside the row body with Save Reset and Delete actions

Scenario: Save sends a PATCH carrying only changed fields
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Save sends PATCH with only changed fields
  Level: unit
  Given the operator edits only the row label
  When the operator clicks Save
  Then the PATCH body contains only the label field

Scenario: Delete rejected with 409 surfaces inline row error
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Delete with 409 surfaces inline error
  Level: unit
  Given the active instance cannot be deleted per the D4c safety 409
  When the operator confirms Delete
  Then the row renders the server detail inline instead of a toast

Scenario: Test connection records Connected status on the row
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Test connection fires POST and records result as Connected
  Level: unit
  Given the operator clicks Test connection on a row
  When the server returns ok true with a latency
  Then the row status dot switches to the connected variant

Scenario: Add instance happy path POSTs and re-fetches the list
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Add instance happy path: POST with supplied id, then re-fetch + expand new row
  Level: unit
  Given the add-instance modal is open with a fresh id
  When the operator submits
  Then a POST to api llm providers fires with the id and the list reloads with the new row

Scenario: Add instance duplicate id surfaces 409 inline
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Add instance duplicate id: surfaces inline error
  Level: unit
  Given the server returns 409 on the create call
  When the operator submits the add-instance form
  Then the modal shows the server detail inline

Scenario: Add instance unknown plugin surfaces 400 inline
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::Add instance unknown plugin: surfaces 400 inline
  Level: unit
  Given the server returns 400 on the create call
  When the operator submits the add-instance form
  Then the modal shows the server detail inline

Scenario: Client-side instance id validator rejects dots and invalid characters
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts::client-side id validator rejects dots and short ids
  Level: unit
  Given the isValidInstanceId helper from the api module
  When the caller passes ids containing a dot or starting with a dash or uppercase letters
  Then the helper returns false for all invalid forms

## Out of Scope

- Renaming an instance's id — requires a delete + create per D4c.
- Rebinding an instance to a different plugin — same as above.
- Server-side sessions / multi-tenant state — GOAL.md non-goals
  through 1.0.
- Playwright end-to-end scenarios — deferred until the test harness
  grows a browser-aware runner; vitest covers the row state machine.
