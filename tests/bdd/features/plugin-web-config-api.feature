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

  Scenario: GET api sessions lists persisted tapes for the current workspace
    Given an admin router wired to a live SessionStore with one appended user message
    When a client GETs api sessions
    Then the response lists one row with id entry_count and tape_name populated

  Scenario: GET api sessions returns 503 when the adapter has no session store wired
    Given an admin router with session_store None and workspace None
    When a client GETs api sessions
    Then the response status is 503

  Scenario: Session list rows include a preview sourced from the first user message
    Given a SessionStore with a tape whose first message is from a user
    When a client GETs api sessions
    Then the row carries a preview field equal to the user message content

  Scenario: GET api sessions id messages returns the projected history
    Given a SessionStore with a tape carrying alternating user and assistant messages
    When a client GETs api sessions id messages for that tape
    Then the response messages list mirrors the loop projection in tape order

  Scenario: Messages endpoint elides history before a compaction anchor
    Given a SessionStore with a tape whose entries straddle a compaction anchor
    When a client GETs api sessions id messages for that tape
    Then the response replaces pre compaction entries with a system summary row

  Scenario: Messages endpoint 404s when the session id is unknown
    Given an admin router wired to an empty SessionStore
    When a client GETs api sessions id messages for a missing id
    Then the response status is 404

  Scenario: Messages endpoint 503s when no session store is wired
    Given an admin router with session_store None and workspace None
    When a client GETs api sessions id messages for any id
    Then the response status is 503

  Scenario: DELETE api sessions id archives the tape and drops it from the list
    Given a SessionStore with one persisted tape carrying a user message
    When a client DELETEs api sessions id for that tape
    Then the response status is 204 and the follow up list omits the row

  Scenario: DELETE api sessions id returns 404 when the id is unknown
    Given an admin router wired to an empty SessionStore
    When a client DELETEs api sessions id for an unknown id
    Then the response status is 404

  Scenario: DELETE api sessions id returns 503 when no session store is wired
    Given an admin router with session_store None and workspace None
    When a client DELETEs api sessions id for any id
    Then the response status is 503

  Scenario: PATCH api sessions id writes a name surfaced on subsequent list
    Given a SessionStore with one persisted tape carrying a user message
    When a client PATCHes api sessions id with a name body
    Then the response row and the follow up list both carry the new name

  Scenario: PATCH api sessions id returns 404 when the id is unknown
    Given an admin router wired to an empty SessionStore
    When a client PATCHes api sessions id with a name body for an unknown id
    Then the response status is 404

  Scenario: PATCH api sessions id returns 400 when the name is blank
    Given a SessionStore with one persisted tape carrying a user message
    When a client PATCHes api sessions id with a whitespace only name
    Then the response status is 400
