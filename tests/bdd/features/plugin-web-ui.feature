Feature: Web adapter UI redesign

  The bundled web adapter ships a two-column kimi-style UI with a
  sidebar (logo, new-chat, Chat/Settings nav, history, version) and
  a main area that routes between Chat and Settings. Settings
  surfaces the ConfigStore via HTTP — LLM providers, plugins, and
  raw configuration — with schema-driven forms and secret masking.

  Scenarios mirror specs/plugin-web-ui.spec Completion Criteria and
  are kept in sync by scripts/check_feature_sync.py.

  Scenario: Sidebar is part of the shipped static bundle
    Given the packaged web plugin static directory
    When the built bundle is inspected
    Then the shell HTML references a yaya app root element

  Scenario: Settings module is emitted as a separate chunk
    Given the packaged web plugin static directory
    When the assets directory is inspected
    Then a settings-view chunk is present alongside the entry bundle

  Scenario: Provider switch is plumbed through the API client
    Given a store subscriber has been disposed
    When a later value is set on the store
    Then the disposed subscriber is not invoked

  Scenario: Plugin toggle round-trips through the store primitive
    Given a store seeded with a numeric counter
    When the counter is patched with a functional updater
    Then the stored value reflects the updater result

  Scenario: Secret fields are masked by the schema form heuristics
    Given the schema form secret heuristic
    When fields named api_key auth_token client_secret and user_password are checked
    Then each field is flagged as a secret

  Scenario: Theme tokens honour prefers-color-scheme
    Given the built CSS bundle
    When the stylesheet is inspected for theme tokens
    Then it declares a prefers-color-scheme dark override

  Scenario: Instance save diff skips unchanged fields
    Given a server row and a draft that matches it exactly
    When the save diff is computed
    Then the resulting patch is empty so no PATCH is sent

  Scenario: Instance save diff emits only the fields that diverged
    Given a server row and a draft that changed only the model field
    When the save diff is computed
    Then the patch contains only the model field

  Scenario: Instance id suggester picks the next free counter suffix
    Given a provider list where the base id and -2 suffix are both taken
    When a new instance id is suggested for the plugin
    Then the returned id uses the next free counter suffix
