spec: task
name: "kernel-config"
tags: [kernel, config]
---

## Intent

Every plugin needs settings — API keys, endpoints, models, bind port,
the plugin enable/disable list. Without a clear config story, `yaya
serve` has nowhere to read them from and first-run fails silently.
The kernel ships a single ordered resolver that merges, in priority
order: CLI flags (per command) → `YAYA_*` environment variables →
`$XDG_CONFIG_HOME/yaya/config.toml` → built-in defaults declared on
`KernelConfig`. Plugin-scoped sub-trees are exposed via
`KernelContext.config` so plugins read their own namespace through
the same accessor whether the value came from env or TOML.

## Decisions

- `KernelConfig` is a `pydantic_settings.BaseSettings` with the env
  prefix `YAYA_` and the nested-key delimiter `__`, so
  `YAYA_LLM_OPENAI__MODEL=gpt-4o` lifts into the `llm_openai` plugin
  sub-tree under key `model`.
- The TOML path defaults to `$XDG_CONFIG_HOME/yaya/config.toml` (or
  `~/.config/yaya/config.toml`). Absent is fine — no auto-create on
  first run.
- A custom `_NestedEnvExtras` settings source captures
  `YAYA_<NS>__<KEY>` env vars where `<NS>` is not a declared kernel
  field and groups them as nested dicts under `model_extra` (because
  pydantic-settings only honours `env_nested_delimiter` for declared
  fields).
- `KernelConfig.plugin_config(name)` returns a defensive copy of the
  named sub-tree (or `{}` for unknown plugins) — plugins must
  tolerate an empty config.
- `PluginRegistry` accepts an optional `kernel_config` and feeds the
  per-plugin sub-tree to each `KernelContext`, so `ctx.config` is now
  populated automatically — previously empty.
- `yaya config show [--json]` prints the resolved config with secret
  redaction. Keys matching `r".*(token|key|secret|password|passphrase).*"`
  case-insensitively render as `"***"`. Recursion walks nested
  mappings and lists.
- No `yaya config set` / `edit` — the TOML file is user-owned;
  `config show` is read-only.

## Boundaries

### Allowed Changes
- src/yaya/kernel/config.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/__init__.py
- src/yaya/cli/commands/config.py
- src/yaya/cli/commands/serve.py
- tests/kernel/test_config.py
- tests/cli/test_config.py
- specs/kernel-config.spec
- docs/dev/architecture.md
- docs/wiki/log.md

### Forbidden
- src/yaya/kernel/bus.py
- src/yaya/kernel/loop.py
- src/yaya/kernel/events.py
- src/yaya/kernel/plugin.py
- src/yaya/plugins/
- GOAL.md

## Completion Criteria

Scenario: AC-01 env var overrides TOML file value
  Test:
    Package: yaya
    Filter: tests/kernel/test_config.py::test_env_var_overrides_file
  Level: unit
  Given a TOML file at the resolved CONFIG_PATH sets llm_openai.model = "gpt-4"
  And the env var YAYA_LLM_OPENAI__MODEL = "gpt-4o" is set
  When the kernel loads config via load_config()
  Then KernelConfig.plugin_config("llm_openai")["model"] equals "gpt-4o"

Scenario: AC-02 yaya config show redacts secrets under JSON
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_json_redacts_openai_api_key
  Level: unit
  Given the env var YAYA_LLM_OPENAI__API_KEY = "sk-abc123" is set
  When the user runs yaya --json config show
  Then the output does not contain "sk-abc123"
  And the resolved value for the api_key key is "***"

Scenario: TOML overrides built-in defaults
  Test:
    Package: yaya
    Filter: tests/kernel/test_config.py::test_file_overrides_defaults
  Level: unit
  Given a TOML file at CONFIG_PATH sets port = 8080 and log_level = "DEBUG"
  When load_config is called
  Then KernelConfig.port equals 8080 and KernelConfig.log_level equals "DEBUG"

Scenario: Plugin namespace lifts arbitrary env vars into a sub-tree
  Test:
    Package: yaya
    Filter: tests/kernel/test_config.py::test_plugin_namespace_via_env
  Level: unit
  Given the env vars YAYA_LLM_OPENAI__MODEL and YAYA_LLM_OPENAI__API_KEY are set
  When load_config is called
  Then plugin_config("llm_openai") contains both keys with their string values

Scenario: Unknown plugin name returns an empty mapping
  Test:
    Package: yaya
    Filter: tests/kernel/test_config.py::test_plugin_config_returns_empty_for_unknown_plugin
  Level: unit
  Given a fresh KernelConfig with no extras
  When plugin_config is called for a plugin name that has no env or TOML entry
  Then the returned mapping is empty and not None

Scenario: Secret-key regex catches token, key, secret, password variants
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_secret_regex_catches_variants
  Level: unit
  Given the redaction predicate _is_secret_key
  When called with api_key, x_token, SECRET_PASSPHRASE, or PASSWORD variants
  Then every variant is flagged as a secret

## Out of Scope

- Per-plugin schema validation — each plugin owns its own validation
  inside `on_load` against its sub-tree.
- A `yaya config set` / `edit` command — the TOML file is user-owned.
- Strategy dispatch reading `ctx.config["strategy"]` — config plumbing
  ships here; the dispatcher wiring ships separately.
