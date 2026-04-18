Feature: Kernel config resolution

  These scenarios mirror specs/kernel-config.spec and keep the config
  resolver's precedence, plugin namespace handling, and CLI redaction
  contract executable.

  Scenario: AC-01 env var overrides TOML file value
    Given a TOML file at the resolved CONFIG_PATH sets llm_openai.model = "gpt-4"
    And the env var YAYA_LLM_OPENAI__MODEL = "gpt-4o" is set
    When the kernel loads config via load_config()
    Then KernelConfig.plugin_config("llm_openai")["model"] equals "gpt-4o"

  Scenario: AC-02 yaya config show redacts secrets under JSON
    Given the env var YAYA_LLM_OPENAI__API_KEY = "sk-abc123" is set
    When the user runs yaya --json config show
    Then the output does not contain "sk-abc123"
    And the resolved value for the api_key key is "***"

  Scenario: TOML overrides built-in defaults
    Given a TOML file at CONFIG_PATH sets port = 8080 and log_level = "DEBUG"
    When load_config is called
    Then KernelConfig.port equals 8080 and KernelConfig.log_level equals "DEBUG"

  Scenario: Plugin namespace lifts arbitrary env vars into a sub-tree
    Given the env vars YAYA_LLM_OPENAI__MODEL and YAYA_LLM_OPENAI__API_KEY are set
    When load_config is called
    Then plugin_config("llm_openai") contains both keys with their string values

  Scenario: Unknown plugin name returns an empty mapping
    Given a fresh KernelConfig with no extras
    When plugin_config is called for a plugin name that has no env or TOML entry
    Then the returned mapping is empty and not None

  Scenario: Secret-key regex catches token, key, secret, password variants
    Given the redaction predicate _is_secret_key
    When called with api_key, x_token, SECRET_PASSPHRASE, or PASSWORD variants
    Then every variant is flagged as a secret
