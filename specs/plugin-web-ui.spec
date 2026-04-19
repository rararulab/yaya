spec: task
name: "plugin-web-ui"
tags: [plugin, web, ui]
---

## Intent

The bundled `web` adapter plugin ships a kimi-cli-inspired two-column
user interface: a collapsible left sidebar (logo, new-chat button,
Chat/Settings navigation, recent-chat list, version footer) and a
main area that routes between the Chat surface and a multi-tab
Settings surface. Settings exposes the repository's ConfigStore
(PR #105) via HTTP (PR B) — users switch LLM providers, toggle
plugins, and edit raw configuration without leaving the browser.
The UI is client-side only: the Python adapter remains a static-file
server plus WebSocket bridge; all state is reactive through a tiny
`createStore` primitive and schema-driven forms. The bundle ships
pre-built in the Python wheel so end users install via `pip`
without Node.

## Decisions

- **Two-column layout.** Grid-based shell: fixed sidebar
  (240 px, collapsible to 56 px) + flexible main area.
- **Hash routing.** `#/chat` and `#/settings`; the Settings module
  is a separate dynamic import so chat-only users do not pay for
  its bundle (Vite emits it as its own chunk).
- **Neutral-light palette.** CSS custom properties
  (`--yaya-sidebar-bg`, `--yaya-main-bg`, `--yaya-accent`, ...)
  with `prefers-color-scheme: dark` media-query override plus an
  explicit `.dark` class on `<html>` toggled from the sidebar.
- **Empty-state hero.** When the chat transcript is empty, render a
  large `yaya` wordmark, tagline, and three hardcoded quick-start
  chips that prefill the prompt input on click.
- **Settings tabs.** LLM Providers (radio-switch + per-provider
  config form + test-connection button), Plugins (enabled toggle +
  schema-driven config + install/remove), Advanced (raw config
  grid with prefix filter + secret reveal toggle).
- **Schema-driven forms.** Shallow (depth 1) JSON Schema renderer:
  `string` → text input (password variant if the key ends in
  `_key`/`_token`/`_secret`/`_password`), `integer`/`number` →
  number input, `boolean` → checkbox, `array`/`object` → JSON
  textarea with parse-on-commit. Missing schema falls back to a
  generic key-value grid.
- **Tiny store primitive.** `createStore<T>()` returns
  `{get, set, patch, subscribe}` — a Set of listeners over one
  value. No framework; no Redux; no Vue/React.
- **API client tolerance.** The REST client returns ApiError with
  a status field; 404/501 render a banner instead of throwing.
- **Dependency-rule preservation.** The existing pi-web-ui
  blacklist still applies (no ChatPanel/Agent/SettingsStore
  imports). The new surfaces use only `<yaya-bubble>`,
  `<console-block>`, `mini-lit` primitives, and Lit directly.
- **Pre-built assets committed.** Every PR that changes `src/`
  rebuilds and commits `static/`; the Python wheel ships Node-free.

## Boundaries

### Allowed Changes
- src/yaya/plugins/web/index.html
- src/yaya/plugins/web/src/app-shell.ts
- src/yaya/plugins/web/src/settings-view.ts
- src/yaya/plugins/web/src/schema-form.ts
- src/yaya/plugins/web/src/store.ts
- src/yaya/plugins/web/src/api.ts
- src/yaya/plugins/web/src/app.css
- src/yaya/plugins/web/src/chat-shell.ts
- src/yaya/plugins/web/src/main.ts
- src/yaya/plugins/web/src/__tests__/setup.ts
- src/yaya/plugins/web/src/__tests__/store.test.ts
- src/yaya/plugins/web/src/__tests__/schema-form.test.ts
- src/yaya/plugins/web/static/
- src/yaya/plugins/web/AGENT.md
- specs/plugin-web-ui.spec
- tests/bdd/features/plugin-web-ui.feature
- tests/bdd/test_plugin_web_ui.py
- tests/plugins/web/test_web_adapter.py
- docs/dev/web-ui.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/strategy_react/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/llm_openai/
- src/yaya/plugins/tool_bash/
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Sidebar is part of the shipped static bundle
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_ui_sidebar_present
  Level: unit
  Given the packaged web plugin static directory
  When the built bundle is inspected
  Then the shell HTML references a yaya app root element

Scenario: Settings module is emitted as a separate chunk
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_ui_settings_chunk_present
  Level: unit
  Given the packaged web plugin static directory
  When the assets directory is inspected
  Then a settings-view chunk is present alongside the entry bundle

Scenario: Provider switch is plumbed through the API client
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/store.test.ts::unsubscribe stops further notifications
  Level: unit
  Given a store subscriber has been disposed
  When a later value is set on the store
  Then the disposed subscriber is not invoked

Scenario: Plugin toggle round-trips through the store primitive
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/store.test.ts::supports functional patch
  Level: unit
  Given a store seeded with a numeric counter
  When the counter is patched with a functional updater
  Then the stored value reflects the updater result

Scenario: Secret fields are masked by the schema form heuristics
  Test:
    Package: yaya
    Filter: src/yaya/plugins/web/src/__tests__/schema-form.test.ts::flags fields ending in _key _token _secret _password as secret
  Level: unit
  Given the schema form secret heuristic
  When fields named api_key auth_token client_secret and user_password are checked
  Then each field is flagged as a secret

Scenario: Theme tokens honour prefers-color-scheme
  Test:
    Package: yaya
    Filter: tests/plugins/web/test_web_adapter.py::test_ui_theme_tokens_present
  Level: unit
  Given the built CSS bundle
  When the stylesheet is inspected for theme tokens
  Then it declares a prefers-color-scheme dark override

## Out of Scope

- Persistent chat history across sessions — stubbed via
  localStorage today; real server-side sessions land with the
  `/api/sessions` endpoint.
- Playwright end-to-end scenarios — deferred until the test
  harness has a browser-aware runner; vitest + static-bundle
  assertions cover the core contract.
- Internationalization and right-to-left layout.
- Public-bind / auth — GOAL.md non-goals through 1.0.
