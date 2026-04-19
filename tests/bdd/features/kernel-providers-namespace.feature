Feature: Kernel providers.<id>.* namespace (multi-instance llm-provider)

  These scenarios mirror specs/kernel-providers-namespace.spec and
  keep the providers-instance data model + bootstrap lift contract
  executable. Instance CRUD, HTTP API, and UI affordances land in
  D4c / D4d and are out of scope here.

  Scenario: AC-01 empty store bootstrap seeds one instance per loaded llm-provider
    Given a registry with one llm-provider plugin and an empty config store
    When the registry starts and the bootstrap pass runs
    Then a providers.<plugin-name>.plugin row is written and the seeded marker is stamped

  Scenario: AC-02 ProvidersView list_instances returns seeded rows
    Given a ConfigStore populated with two providers.<id> subtrees
    When list_instances is called on a ProvidersView over that store
    Then both instance ids are returned in sorted order with meta fields parsed

  Scenario: AC-03 instances_for_plugin filters by backing plugin name
    Given a ConfigStore with two instances backed by llm-openai and one by llm-echo
    When instances_for_plugin("llm-openai") is called
    Then only the two llm-openai instances are returned

  Scenario: AC-04 bootstrap is idempotent across restarts
    Given a config store that already carries the providers-seeded marker
    When the registry runs the bootstrap pass a second time
    Then no additional rows are written and the marker timestamp is unchanged

  Scenario: AC-05 active_id reads the current provider config value
    Given a ConfigStore with the provider key set to a known instance id
    When active_id is read on a ProvidersView over that store
    Then the returned value matches the provider key and flips when the key is updated

  Scenario: AC-06 bootstrap lifts legacy plugin.<name>.api_key into providers.<name>.api_key
    Given a config store with plugin.llm_openai.api_key already populated
    When the registry runs bootstrap with an llm-openai plugin loaded
    Then providers.llm-openai.api_key holds the lifted value while the legacy row stays in place
