spec: task
name: "kernel-providers-namespace"
tags: [kernel, config, providers, llm-provider]
---

## Intent

Before #116 the kernel only understood one active ``llm-provider``
plugin at a time: the flat ``plugin.<name>.*`` config tree could not
hold two "OpenAI prod" and "Azure OpenAI" records for the same
``llm-openai`` plugin, and the ``provider`` key named a plugin rather
than a specific configured instance. This spec reserves the
``providers.<id>.*`` namespace in the existing :class:`ConfigStore`
so one plugin can back many configured instances, without touching
any plugin behaviour — D4b flips plugins to read via
``ctx.providers``, D4c adds HTTP CRUD, D4d wires the UI.

## Decisions

- Schema is flat, not nested: each instance lives under
  ``providers.<id>.{plugin, label, <field>...}`` rows. Meta fields
  (``plugin``, ``label``) are separated from schema fields at parse
  time so ``InstanceRow.config`` surfaces only the plugin-facing
  subset.
- ``<id>`` is any dotted-safe string. Bootstrap uses the plugin name
  verbatim so seeded instance ids match existing ``ctx.config``
  expectations; operator-created instances use whatever id the UI /
  HTTP API assigns.
- :class:`ProvidersView` is read-only and lives in
  ``yaya.kernel.providers``. It parses the live :class:`ConfigStore`
  cache on every call so subsequent ``set`` writes are visible
  without cache invalidation. Writes go through the existing
  :class:`ConfigStore` API — D4c layers an HTTP CRUD shape on top.
- :class:`KernelContext` gains a ``providers`` property that
  returns a fresh :class:`ProvidersView` bound to the live store, or
  ``None`` when the context was built without a store.
- Bootstrap runs once per install, guarded by the
  ``_meta.providers_seeded_at`` marker. For each loaded
  ``llm-provider`` plugin it writes
  ``providers.<plugin.name>.plugin`` + ``label`` meta rows and lifts
  ``plugin.<ns>.*`` legacy rows into
  ``providers.<plugin.name>.*``. Original ``plugin.<ns>.*`` rows
  stay in place — D4b switches the plugins to instance-scoped
  reads before we garbage-collect the legacy rows.
- When the ``provider`` key is unset after seeding, bootstrap
  defaults it to the first seeded instance id so
  ``strategy_react`` resolves an active provider without operator
  intervention.
- No new public event kinds: ``providers.*`` writes ride the
  existing ``config.updated`` event so subscribers that already
  filter on ``key.startswith("providers.")`` wake up naturally.

## Boundaries

### Allowed Changes
- src/yaya/kernel/providers.py
- src/yaya/kernel/plugin.py
- src/yaya/kernel/registry.py
- src/yaya/kernel/__init__.py
- src/yaya/kernel/AGENT.md
- tests/kernel/test_providers_view.py
- tests/kernel/test_providers_bootstrap.py
- tests/bdd/features/kernel-providers-namespace.feature
- specs/kernel-providers-namespace.spec
- docs/dev/plugin-protocol.md

### Forbidden
- src/yaya/plugins/
- src/yaya/kernel/bus.py
- src/yaya/kernel/loop.py
- src/yaya/kernel/config_store.py
- GOAL.md

## Completion Criteria

Scenario: AC-01 empty store bootstrap seeds one instance per loaded llm-provider
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_bootstrap.py::test_bootstrap_seeds_one_instance_per_plugin
  Level: unit
  Given a registry with one llm-provider plugin and an empty config store
  When the registry starts and the bootstrap pass runs
  Then a providers.<plugin-name>.plugin row is written and the seeded marker is stamped

Scenario: AC-02 ProvidersView list_instances returns seeded rows
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_view.py::test_list_instances_returns_seeded_rows
  Level: unit
  Given a ConfigStore populated with two providers.<id> subtrees
  When list_instances is called on a ProvidersView over that store
  Then both instance ids are returned in sorted order with meta fields parsed

Scenario: AC-03 instances_for_plugin filters by backing plugin name
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_view.py::test_instances_for_plugin_filters
  Level: unit
  Given a ConfigStore with two instances backed by llm-openai and one by llm-echo
  When instances_for_plugin("llm-openai") is called
  Then only the two llm-openai instances are returned

Scenario: AC-04 bootstrap is idempotent across restarts
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_bootstrap.py::test_bootstrap_idempotent
  Level: unit
  Given a config store that already carries the providers-seeded marker
  When the registry runs the bootstrap pass a second time
  Then no additional rows are written and the marker timestamp is unchanged

Scenario: AC-05 active_id reads the current provider config value
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_view.py::test_active_id_reads_provider_key
  Level: unit
  Given a ConfigStore with the provider key set to a known instance id
  When active_id is read on a ProvidersView over that store
  Then the returned value matches the provider key and flips when the key is updated

Scenario: AC-06 bootstrap lifts legacy plugin.<name>.api_key into providers.<name>.api_key
  Test:
    Package: yaya
    Filter: tests/kernel/test_providers_bootstrap.py::test_bootstrap_lifts_legacy_fields
  Level: unit
  Given a config store with plugin.llm_openai.api_key already populated
  When the registry runs bootstrap with an llm-openai plugin loaded
  Then providers.llm-openai.api_key holds the lifted value while the legacy row stays in place

## Out of Scope

- Mutation API for providers (create / update / delete an instance)
  — lands in D4c as ``POST/PATCH/DELETE /api/providers/<id>``.
- UI affordances for switching the active instance — lands in D4d.
- Per-plugin schema validation — each llm-provider plugin is
  responsible for rejecting unknown / malformed fields when it
  reads its instance in D4b.
- Garbage-collecting the legacy ``plugin.<name>.*`` rows — a
  follow-up PR after D4b confirms no plugin still reads the
  legacy sub-tree.
