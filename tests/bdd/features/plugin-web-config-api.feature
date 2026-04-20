Feature: Web adapter HTTP admin API

  The bundled web adapter exposes HTTP endpoints over the kernel's
  live ConfigStore and PluginRegistry so the browser UI can drive
  config, plugin, and LLM provider management without a restart.

  Scenarios mirror specs/plugin-web-config-api.spec Completion
  Criteria and are kept in sync by scripts/check_feature_sync.py.

  Scenario: Config CRUD round-trips through the API
    Given an admin router wired to a live config store
    When a client issues a PATCH then GET then DELETE for a config key
    Then each response reflects the new state and the final GET returns 404

  Scenario: Secret-suffix keys mask unless show flag is set
    Given an admin router wired to a live config store containing a secret key
    When a client reads the key with and without the show flag
    Then the default response is masked and the show flag reveals the value

  Scenario: Plugin list exposes enabled flag and current config
    Given an admin router wired to a stub registry and a live store
    When a client issues GET api plugins
    Then the response includes enabled status schema and current_config per plugin

  Scenario: Patching enabled writes the plugin enabled config key
    Given an admin router wired to a stub registry and a live store
    When a client PATCHes api plugins with enabled false
    Then the store records plugin ns enabled false and the response signals reload_required

  Scenario: Plugin install delegates to the registry
    Given an admin router wired to a stub registry
    When a client POSTs api plugins install with a valid source
    Then the stub registry records the install call and the response is ok

  Scenario: Switching the active LLM provider writes the provider config key
    Given an admin router wired to a config store seeded with llm provider instances
    When a client PATCHes api llm providers active with an instance id
    Then the store records provider equal to that instance id and the response is the refreshed list

  Scenario: LLM provider test endpoint round-trips through the bus
    Given an admin router wired to a bus whose stub provider echoes llm call response for an instance id
    When a client POSTs api llm providers id test
    Then the llm call request carries a bridge web-api-test session id and the response is ok true with latency_ms

  Scenario: llm_openai hot reload preserves in flight calls
    Given a running llm openai plugin with a live client
    When a config updated event triggers a client rebuild
    Then the previous client is never closed and a fresh client is built
