Feature: Kernel logging and error taxonomy

  These scenarios mirror specs/kernel-logging.spec and keep the logging
  sink, redaction, intercept, and plugin.error attribution contracts
  executable.

  Scenario: configure logging is idempotent
    Given a fresh KernelConfig with log_level INFO
    When configure_logging is called twice in a row
    Then the loguru handler count after the second call equals the count after the first

  Scenario: redaction filter scrubs secret keys
    Given a record with secret-looking keys api_key, x_token, SECRET_PASSPHRASE, PASSWORD, openai_token
    When the redaction filter walks record["extra"]
    Then every secret value is replaced with the literal "***"
    And non-secret keys (like model) are preserved

  Scenario: JSON mode emits valid JSON per line
    Given the env var YAYA_LOG_JSON = "1" is set
    When configure_logging runs and a plugin logger emits a line
    Then the stderr line parses as JSON with message, plugin, level fields

  Scenario: file sink rotates at the size limit
    Given configure_logging has wired the rotated file sink under tmp_path
    When more than 10 MiB of log content is written
    Then a backup file appears alongside yaya.log

  Scenario: stdlib logging intercept routes to loguru
    Given configure_logging has installed the InterceptHandler on the root logger
    When a record is emitted via logging.getLogger("third_party").info(...)
    Then the message appears on the loguru stderr sink

  Scenario: PluginError event carries kind and traceback hash
    Given a bus subscriber that raises PluginError("boom")
    When the bus delivers a matching event
    Then a plugin.error event is emitted with kind="PluginError" and an 8-char hex error_hash
