Feature: Kernel config store (live, hot-reload)

  These scenarios mirror specs/kernel-config-store.spec and keep the
  SQLite KV store, the config.updated event, and the CLI hot-reload
  contract executable.

  Scenario: AC-01 set + get round-trips JSON-safe values
    Given an open ConfigStore backed by a tmp sqlite file
    When the caller sets scalar, list, and dict values and reads each back
    Then every read returns the original Python value

  Scenario: AC-02 set emits config.updated on the kernel session
    Given a ConfigStore wired to a running EventBus
    When the caller sets plugin.llm_openai.base_url
    Then a config.updated event is published on session_id="kernel" with the key and prefix hint

  Scenario: AC-03 ConfigView reflects live writes
    Given a ConfigView built from a ConfigStore
    When a subsequent set writes a new key
    Then the view surfaces the new key without re-construction

  Scenario: AC-04 unset is idempotent
    Given a ConfigStore with one key already set
    When the caller unsets that key twice
    Then the first call returns True and the second returns False

  Scenario: AC-05 list_prefix filters and sorts
    Given several keys set under different dotted namespaces
    When list_prefix is called with a specific prefix
    Then only the matching keys are returned in sorted order

  Scenario: AC-06 values persist across reopen
    Given keys written to a ConfigStore that was then closed
    When the same DB path is reopened
    Then every value is still present

  Scenario: AC-07 CLI set then get round-trips
    Given a yaya config store redirected under tmp_path
    When the user runs yaya config set provider openai then yaya config get provider
    Then the get output contains "openai"

  Scenario: AC-08 CLI list filters by prefix
    Given multiple keys set under plugin.a and plugin.b
    When the user runs yaya --json config list plugin.a.
    Then only the plugin.a.* keys appear in the JSON entries

  Scenario: AC-09 CLI unset is idempotent across invocations
    Given a key that was just set via the CLI
    When the user runs yaya config unset twice for the same key
    Then the first invocation reports removed=true and the second reports removed=false

  Scenario: AC-10 CLI list -v masks secret-suffixed keys
    Given the CLI stored a plugin.llm_openai.api_key value
    When the user runs yaya --json config list plugin.llm_openai. -v
    Then the value is masked to ****<last4> unless --show-secrets is also passed

  Scenario: AC-11 first boot migrates KernelConfig into the DB
    Given an empty ConfigStore and a flattened KernelConfig
    When migrate_from_kernel_config is called
    Then the declared kernel fields land in the DB and _meta.migrated_from_toml_at is stamped

  Scenario: AC-12 second migration is a no-op
    Given a ConfigStore that has already been migrated once
    When migrate_from_kernel_config is called again
    Then the second call writes zero rows

  Scenario: AC-13 non-JSON values are rejected
    Given an open ConfigStore
    When the caller sets a value that is not JSON-encodable
    Then the call raises TypeError

  Scenario: AC-14 strategy_react hot-switches provider between decisions
    Given ReActStrategy loaded against a live ConfigStore scoped view
    When the operator flips plugin.strategy_react.provider between two strategy.decide.request events
    Then the second strategy.decide.response carries the new provider name

  Scenario: AC-15 llm_openai rebuilds the client on config.updated
    Given an OpenAIProvider loaded with a stubbed AsyncOpenAI and an initial base_url
    When plugin.llm_openai.base_url is set to a new value and config.updated is delivered
    Then the plugin rebuilds its client with the new base_url and non-matching keys do not rebuild
