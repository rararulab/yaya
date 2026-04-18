spec: task
name: "kernel-logging"
tags: [kernel, logging, errors]
---

## Intent

Every production pain point starts at debugging. yaya needs structured
logging and a closed error taxonomy in the kernel before plugins pile
up so failures are legible across the bus from day 1. Loguru is the
single sink-management surface; stdlib `logging` records are routed
through an intercept handler so third-party libs still appear in the
unified stream. Each plugin receives a logger pre-bound with
`plugin=<name>` so grep-by-plugin is trivial. A redaction filter
scrubs secret-looking fields by key (`token` / `key` / `secret` /
`password` / `passphrase`) and by value (`sk-...`, `Bearer ...`)
before any record reaches a sink. Bus-side: when a handler raises a
`PluginError`, the synthesized `plugin.error` event carries the
exception subclass name plus an 8-char SHA-1 traceback hash so noisy
plugins can be deduped in a log scrape.

## Decisions

- `configure_logging(config)` is idempotent. Calling it twice does
  not stack additional sinks (relies on `logger.remove()` + a
  module-level flag).
- Stderr sink: level from `KernelConfig.log_level`, coloured rich
  format when `stderr.isatty()`, plain format otherwise. JSON
  per-line format when `YAYA_LOG_JSON=1`.
- File sink: `$XDG_STATE_HOME/yaya/logs/yaya.log`, always DEBUG,
  10 MiB rotation × 5 backups. Best-effort: a read-only filesystem
  surfaces a warning and the kernel keeps running.
- Stdlib `logging` is routed into loguru via an intercept handler
  installed on the root logger; depth-walked so the originating
  caller's filename/line survives.
- `KernelContext.logger` is typed as `Any` at the Protocol level so
  loguru does not leak into the Plugin ABI. Runtime value is the
  loguru `Logger` returned by `get_plugin_logger(name)` — plugins
  call standard `info`/`warning`/`error` methods which loguru exposes
  API-compatibly with stdlib.
- Error taxonomy: `YayaError` (base) → `KernelError` (crash-worthy),
  `PluginError` (recoverable; bus-isolated), `ConfigError`
  (user-facing), `YayaTimeoutError` (generic). We deliberately do
  NOT shadow `builtins.TimeoutError` — too many call sites catch it
  with asyncio semantics in mind.
- Bus integration: `EventBus._report_handler_failure` populates
  optional `kind` and `error_hash` payload fields on the synthetic
  `plugin.error` event. `kind` is the exception subclass name for
  `PluginError`; the literal `"plugin_error"` for bare exceptions.
  `error_hash` is `sha1(traceback)[:8]`.
- CLI: `yaya --log-level DEBUG` overrides the config-resolved level
  before `configure_logging` runs. Existing `-v` / `-q` flags still
  work (legacy verbosity tabulation); explicit `--log-level` wins.

## Boundaries

### Allowed Changes
- src/yaya/kernel/logging.py
- src/yaya/kernel/errors.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/bus.py
- src/yaya/kernel/events.py
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/__init__.py
- tests/kernel/test_logging.py
- tests/kernel/test_errors.py
- tests/cli/__snapshots__/test_help_snapshot.ambr
- specs/kernel-logging.spec
- docs/dev/architecture.md
- docs/wiki/log.md

### Forbidden
- src/yaya/kernel/loop.py
- src/yaya/kernel/payload.py
- src/yaya/plugins/
- src/yaya/core/
- GOAL.md

## Completion Criteria

Scenario: configure logging is idempotent
  Test:
    Package: yaya
    Filter: tests/kernel/test_logging.py::test_configure_logging_idempotent
  Level: unit
  Given a fresh KernelConfig with log_level INFO
  When configure_logging is called twice in a row
  Then the loguru handler count after the second call equals the count after the first

Scenario: redaction filter scrubs secret keys
  Test:
    Package: yaya
    Filter: tests/kernel/test_logging.py::test_redaction_filter_scrubs_secret_keys
  Level: unit
  Given a record with secret-looking keys api_key, x_token, SECRET_PASSPHRASE, PASSWORD, openai_token
  When the redaction filter walks record["extra"]
  Then every secret value is replaced with the literal "***"
  And non-secret keys (like model) are preserved

Scenario: JSON mode emits valid JSON per line
  Test:
    Package: yaya
    Filter: tests/kernel/test_logging.py::test_json_mode_emits_valid_json_per_line
  Level: unit
  Given the env var YAYA_LOG_JSON = "1" is set
  When configure_logging runs and a plugin logger emits a line
  Then the stderr line parses as JSON with message, plugin, level fields

Scenario: file sink rotates at the size limit
  Test:
    Package: yaya
    Filter: tests/kernel/test_logging.py::test_file_sink_rotates_at_size_limit
  Level: unit
  Given configure_logging has wired the rotated file sink under tmp_path
  When more than 10 MiB of log content is written
  Then a backup file appears alongside yaya.log

Scenario: stdlib logging intercept routes to loguru
  Test:
    Package: yaya
    Filter: tests/kernel/test_logging.py::test_stdlib_logging_intercept_routes_to_loguru
  Level: unit
  Given configure_logging has installed the InterceptHandler on the root logger
  When a record is emitted via logging.getLogger("third_party").info(...)
  Then the message appears on the loguru stderr sink

Scenario: PluginError event carries kind and traceback hash
  Test:
    Package: yaya
    Filter: tests/kernel/test_errors.py::test_plugin_error_event_carries_hash_and_kind
  Level: unit
  Given a bus subscriber that raises PluginError("boom")
  When the bus delivers a matching event
  Then a plugin.error event is emitted with kind="PluginError" and an 8-char hex error_hash

## Out of Scope

- Remote log shipping (OpenTelemetry, syslog).
- Per-plugin log-level overrides via config — uniform level today;
  follow-up issue when a plugin actually needs a different level.
- Metrics / tracing — that ships separately under observability.
