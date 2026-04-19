spec: task
name: "kernel-config-store"
tags: [kernel, config, hot-reload]
---

## Intent

The boot-time TOML + env resolver (`KernelConfig`) wires plugin
settings at startup — flipping a key requires editing `config.toml`
and restarting `yaya serve`. Issue #104 replaces that with a live,
per-install SQLite KV store at
`${XDG_STATE_HOME:-~/.local/state}/yaya/config.db`: every `yaya
config set` emits a `config.updated` event and plugins subscribed to
the relevant prefix hot-reload without a restart. TOML + env remain
as first-run bootstrap — the store migrates them into the DB on an
empty install and stamps `_meta.migrated_from_toml_at` so subsequent
boots skip migration.

## Decisions

- Single sqlite table: `config(key TEXT PRIMARY KEY, value TEXT NOT NULL,
  updated_at INTEGER NOT NULL)` + `config_prefix` index on `key`.
- Values stored as JSON strings (`json.dumps(sort_keys=True)`);
  non-JSON scalars are rejected at `set()` time with `TypeError`.
- `ConfigStore` owns exactly one `sqlite3.Connection` opened with
  `check_same_thread=False` and a single-worker
  `ThreadPoolExecutor` — every DB call funnels through one thread,
  same pattern as `memory_sqlite`.
- In-memory cache backs every read — `ConfigView` is a `Mapping`
  proxy over that cache, so `ctx.config["key"]` reflects the live
  value without a DB round-trip.
- Every write (`set` / `unset` / `migrate`) emits
  `config.updated` on `session_id="kernel"` per lesson #2, with
  payload `{"key", "prefix_match_hint"}`. Plugins filter by
  `key.startswith("plugin.<name>.")`.
- `PluginRegistry` opens the store **before** plugin discovery so
  `on_load` sees a populated view, closes it **after** every
  `on_unload` runs — caller-supplied stores are left open
  (`_owns_config_store=False`).
- Per-plugin scoped view: the registry hands each plugin
  `store.view(prefix=f"plugin.{ns}.")` where `ns` is the plugin name
  with `-` → `_`. Reads surface keys with the prefix stripped, so
  legacy `ctx.config["api_key"]` keeps working.
- `llm_openai` subscribes to `config.updated` and rebuilds its
  `AsyncOpenAI` client when `plugin.llm_openai.api_key` or
  `plugin.llm_openai.base_url` change.
- `strategy_react._provider_and_model` reads `ctx.config["provider"]`
  / `["model"]` per decision — no cached value — so a live
  `yaya config set plugin.strategy_react.provider anthropic` flips
  the next `strategy.decide.response` with no restart.
- CLI: `yaya config get/set/unset/list [prefix] [-v] [--show-secrets]`.
  `list -v` masks keys whose last dotted segment matches `api_key`,
  `token`, `secret`, or `password` (rendered as `****` or `****<last4>`
  for strings ≥5 chars) unless `--show-secrets` is passed. Single-key
  `get` never redacts — lookup is an explicit opt-in.
- `set <key> <value>` parses `value` as JSON first; on parse failure
  we fall back to the raw string so `yaya config set provider openai`
  lands without shell-quoting.

## Boundaries

### Allowed Changes
- src/yaya/kernel/config_store.py
- src/yaya/kernel/config.py
- src/yaya/kernel/events.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- src/yaya/cli/commands/config.py
- src/yaya/plugins/llm_openai/plugin.py
- src/yaya/plugins/strategy_react/plugin.py
- tests/kernel/test_config_store.py
- tests/kernel/test_strategy_hot_provider.py
- tests/kernel/test_events.py
- tests/cli/test_config.py
- tests/cli/__snapshots__/test_help_snapshot.ambr
- tests/plugins/llm_openai/test_llm_openai.py
- tests/bdd/features/kernel-config-store.feature
- specs/kernel-config-store.spec
- docs/dev/plugin-protocol.md
- docs/dev/cli.md

### Forbidden
- src/yaya/kernel/bus.py
- src/yaya/kernel/loop.py
- src/yaya/kernel/plugin.py
- src/yaya/kernel/session.py
- GOAL.md

## Completion Criteria

Scenario: AC-01 set + get round-trips JSON-safe values
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_set_get_roundtrip
  Level: unit
  Given an open ConfigStore backed by a tmp sqlite file
  When the caller sets scalar, list, and dict values and reads each back
  Then every read returns the original Python value

Scenario: AC-02 set emits config.updated on the kernel session
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_set_emits_config_updated
  Level: unit
  Given a ConfigStore wired to a running EventBus
  When the caller sets plugin.llm_openai.base_url
  Then a config.updated event is published on session_id="kernel" with the key and prefix hint

Scenario: AC-03 ConfigView reflects live writes
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_view_is_live
  Level: unit
  Given a ConfigView built from a ConfigStore
  When a subsequent set writes a new key
  Then the view surfaces the new key without re-construction

Scenario: AC-04 unset is idempotent
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_unset_idempotent
  Level: unit
  Given a ConfigStore with one key already set
  When the caller unsets that key twice
  Then the first call returns True and the second returns False

Scenario: AC-05 list_prefix filters and sorts
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_list_prefix
  Level: unit
  Given several keys set under different dotted namespaces
  When list_prefix is called with a specific prefix
  Then only the matching keys are returned in sorted order

Scenario: AC-06 values persist across reopen
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_persistence_across_reopen
  Level: unit
  Given keys written to a ConfigStore that was then closed
  When the same DB path is reopened
  Then every value is still present

Scenario: AC-07 CLI set then get round-trips
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_config_set_get_roundtrip
  Level: unit
  Given a yaya config store redirected under tmp_path
  When the user runs yaya config set provider openai then yaya config get provider
  Then the get output contains "openai"

Scenario: AC-08 CLI list filters by prefix
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_config_list_prefix
  Level: unit
  Given multiple keys set under plugin.a and plugin.b
  When the user runs yaya --json config list plugin.a.
  Then only the plugin.a.* keys appear in the JSON entries

Scenario: AC-09 CLI unset is idempotent across invocations
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_config_unset_idempotent
  Level: unit
  Given a key that was just set via the CLI
  When the user runs yaya config unset twice for the same key
  Then the first invocation reports removed=true and the second reports removed=false

Scenario: AC-10 CLI list -v masks secret-suffixed keys
  Test:
    Package: yaya
    Filter: tests/cli/test_config.py::test_config_list_masks_secrets_by_default
  Level: unit
  Given the CLI stored a plugin.llm_openai.api_key value
  When the user runs yaya --json config list plugin.llm_openai. -v
  Then the value is masked to ****<last4> unless --show-secrets is also passed

Scenario: AC-11 first boot migrates KernelConfig into the DB
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_toml_migration_writes_marker
  Level: unit
  Given an empty ConfigStore and a flattened KernelConfig
  When migrate_from_kernel_config is called
  Then the declared kernel fields land in the DB and _meta.migrated_from_toml_at is stamped

Scenario: AC-12 second migration is a no-op
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_migration_idempotent
  Level: unit
  Given a ConfigStore that has already been migrated once
  When migrate_from_kernel_config is called again
  Then the second call writes zero rows

Scenario: AC-13 non-JSON values are rejected
  Test:
    Package: yaya
    Filter: tests/kernel/test_config_store.py::test_non_json_value_rejected
  Level: unit
  Given an open ConfigStore
  When the caller sets a value that is not JSON-encodable
  Then the call raises TypeError

Scenario: AC-14 strategy_react hot-switches provider between decisions
  Test:
    Package: yaya
    Filter: tests/kernel/test_strategy_hot_provider.py::test_hot_switch_provider_between_decisions
  Level: unit
  Given ReActStrategy loaded against a live ConfigStore scoped view
  When the operator flips plugin.strategy_react.provider between two strategy.decide.request events
  Then the second strategy.decide.response carries the new provider name

Scenario: AC-15 llm_openai rebuilds the client on config.updated
  Test:
    Package: yaya
    Filter: tests/kernel/test_strategy_hot_provider.py::test_llm_openai_rebuilds_client_on_config_updated
  Level: unit
  Given an OpenAIProvider loaded with a stubbed AsyncOpenAI and an initial base_url
  When plugin.llm_openai.base_url is set to a new value and config.updated is delivered
  Then the plugin rebuilds its client with the new base_url and non-matching keys do not rebuild

## Out of Scope

- Sync-mode config access from plugin constructors — every read
  still goes through the asyncio event loop via the ConfigView
  cache.
- Remote / multi-process config — one DB file per install,
  single-writer semantics apply.
- Schema validation per plugin — plugins own their own validation
  inside `on_load` against their scoped ConfigView.
