# kernel-registry

## Intent

The kernel's plugin registry is the layer that turns an installed Python
package into a live subscriber on yaya's event bus. It reads setuptools
entry points in the ``yaya.plugins.v1`` group, instantiates each declared
``Plugin`` object, wires it into the bus, drives its ``on_load`` /
``on_event`` / ``on_unload`` lifecycle, and isolates repeat-offender
plugins by unloading them and emitting ``plugin.removed`` after a
configurable failure threshold. Bundled plugins and third-party plugins
go through the same code path — "bundled" is only a deterministic
load-order tie-breaker, never a behavioral branch. This contract pins
the registry's observable behavior before any real plugin (bundled web
adapter, tool, LLM provider, …) is built on top.

## Decisions

- Registry lives in ``src/yaya/kernel/registry.py`` as ``PluginRegistry``
  with ``PluginStatus`` (``StrEnum``: ``loaded | failed | unloaded``)
  and an internal ``_PluginRecord`` dataclass (``eq=False``,
  ``slots=True``) — identity-keyed so subscription handles survive
  ``list.remove`` / identity comparisons (lesson #7).
- Entry-point group: ``yaya.plugins.v1`` — frozen in
  ``docs/dev/plugin-protocol.md``. Discovery uses
  ``importlib.metadata.entry_points(group=…)``. Ordering: bundled first
  (distribution name ``yaya``), third-party second, each sub-list sorted
  by entry-point name for determinism. **No special-case branch** for
  bundled plugins; they run the same ``_load_entry_point`` as
  third-party.
- Plugin conformance is checked via ``isinstance(obj, Plugin)`` — works
  because ``Plugin`` is ``@runtime_checkable``. A non-conforming object
  emits ``plugin.error`` with ``error="invalid_plugin_object"`` and is
  skipped.
- Failure threshold default = 3. The registry subscribes to
  ``plugin.error`` with ``source="kernel-registry"`` (not ``"kernel"``,
  which trips the bus's recursion guard on synthetic-error re-emission).
  Each ``plugin.error`` attributed to a loaded plugin increments its
  counter; a **successful** ``on_event`` invocation resets that
  counter to zero, so **N *consecutive* failures** — not N cumulative —
  triggers unload. Default N = 3. Reset runs inside the per-plugin
  bus handler closure (``_make_handler``'s ``on_success`` callback)
  so the success path cannot forget to update failure accounting.
  Once the counter breaches the threshold the registry spawns an
  unload task with ``reason="threshold"`` (record ends in
  ``status=failed``); orderly ``stop()`` / ``remove()`` unloads pass
  ``reason="stop"`` and land in ``status=unloaded`` regardless of any
  lingering ``error_count``.
- The ``remove()`` bundled guard is entry-point-authoritative. The
  registry maintains ``_bundled_names: set[str]`` populated during
  discovery with both ``ep.name`` and ``plugin.name`` (when load
  succeeded), and ``remove()`` refreshes the set from
  ``importlib.metadata.entry_points`` before checking membership. This
  blocks ``remove("<bundled>")`` even when the bundled entry point
  failed to load, or hasn't been discovered yet.
- **Unload task is spawned via ``asyncio.create_task(..., context=
  contextvars.Context())``** so the bus's private ``_IN_WORKER``
  ContextVar resets inside the task. Without the reset,
  ``await bus.publish("plugin.removed", …)`` inside the task would
  fire-and-forget (bus semantics for in-worker callers) and
  ``plugin.removed`` would never reach adapters. Same pattern
  ``AgentLoop._on_user_message`` uses (lesson #2 / ``loop.py``).
- ``install(source, editable=False)`` shells to ``uv pip install`` via
  ``asyncio.create_subprocess_exec`` — never ``shell=True``. Falls back
  to plain ``pip`` if ``uv`` is not on ``PATH``. Input is validated:
  PyPI names, absolute paths, ``file://``, and ``https://`` URLs are
  accepted; anything else (shell metachars, other URL schemes, relative
  paths) raises ``ValueError``. After a successful install, discovery
  runs again so the newly-installed plugin comes online without
  restarting the kernel.
- ``remove(name)`` raises ``ValueError`` if the named plugin is bundled;
  otherwise shells to ``uv pip uninstall -y`` (same subprocess
  plumbing as ``install``), unloads any in-memory record, and re-runs
  discovery.
- ``start()`` emits one ``plugin.loaded`` per successful load and a
  single ``kernel.ready`` after the first-pass discovery finishes.
  ``stop()`` runs ``on_unload`` for every loaded plugin in **reverse**
  load order (last loaded unloads first), then emits ``kernel.shutdown``.
  ``on_unload`` exceptions are logged and swallowed — once we have
  decided to unload, blocking on a plugin's cleanup bug just wedges the
  kernel.
- ``snapshot()`` returns a list of
  ``{"name", "version", "category", "status"}`` dicts in first-seen
  load order, for ``yaya plugin list``.
- Stdlib only. Imports: ``yaya.kernel.bus``, ``yaya.kernel.events``,
  ``yaya.kernel.plugin``. No imports from ``cli``, ``plugins``,
  ``core``, or ``loop``.

## Boundaries

- **Allowed**:
  - ``src/yaya/kernel/registry.py``
  - ``src/yaya/kernel/__init__.py`` (re-export only)
  - ``src/yaya/kernel/AGENT.md`` (registry bullet)
  - ``tests/kernel/test_registry.py``
  - ``specs/kernel-registry.spec.md``
- **Forbidden**: every other path. This PR must not touch
  ``src/yaya/cli/``, ``src/yaya/core/``, ``src/yaya/plugins/``,
  ``pyproject.toml`` dependencies, ``docs/dev/plugin-protocol.md``, or
  ``GOAL.md``.

## Completion Criteria (BDD)

Scenario: Entry-point discovery loads a plugin
  Given a Plugin object exposed via a ``yaya.plugins.v1`` entry point
  When the registry is started
  Then the plugin's ``on_load`` is called once
  And a ``plugin.loaded`` event is emitted with its name, version, category
  Test: tests/kernel/test_registry.py::test_entry_point_discovery_loads_plugin

Scenario: Bundled plugin uses the same code path
  Given one bundled and one third-party plugin registered under the same entry-point group
  When the registry is started
  Then both plugins run through ``_load_entry_point`` with identical behavior
  And the bundled plugin appears first in ``snapshot()``
  Test: tests/kernel/test_registry.py::test_bundled_plugin_uses_same_load_path

Scenario: Repeated failures unload the plugin
  Given a plugin whose ``on_event`` raises every time
  And a failure threshold of 3
  When three events of a subscribed kind are delivered to that plugin
  Then the registry unloads the plugin and emits ``plugin.removed``
  And the plugin's ``on_unload`` is called exactly once
  And its status in ``snapshot()`` is ``"failed"``
  Test: tests/kernel/test_registry.py::test_repeated_failures_unload_plugin

Scenario: Registry snapshot lists every plugin with status
  Given two plugins that load successfully and one that raises in ``on_load``
  When ``registry.snapshot()`` is called
  Then three entries are returned carrying ``name``, ``version``, ``category``, ``status``
  And the failing plugin's status is ``"failed"``
  Test: tests/kernel/test_registry.py::test_snapshot_lists_every_plugin_with_status

Scenario: Install shells to uv pip without a shell
  Given an ``uv`` binary on ``PATH``
  When ``registry.install("yaya-tool-bash")`` is called
  Then ``asyncio.create_subprocess_exec`` is invoked with ``uv pip install yaya-tool-bash``
  And discovery re-runs so a freshly installed plugin comes online
  Test: tests/kernel/test_registry.py::test_install_invokes_subprocess_and_refreshes

Scenario: Remove refuses to uninstall a bundled plugin
  Given a bundled plugin loaded through yaya's own distribution entry points
  When ``registry.remove("<bundled-name>")`` is called
  Then ``ValueError`` is raised referencing "bundled"
  Test: tests/kernel/test_registry.py::test_remove_bundled_plugin_raises

Scenario: Stop unloads in reverse load order
  Given two plugins loaded in order A then B
  When ``registry.stop()`` is called
  Then ``on_unload`` is invoked on B before A
  And ``kernel.shutdown`` is emitted
  Test: tests/kernel/test_registry.py::test_stop_runs_on_unload_in_reverse_order

Scenario: Install source validation rejects shell metacharacters
  Given a source string containing ``;`` or other shell metachars
  When ``registry.install(source)`` is called
  Then ``ValueError`` is raised before any subprocess is spawned
  Test: tests/kernel/test_registry.py::test_validate_install_source_rejects_hazards

## Out of Scope

- Hot-reload of an already-loaded plugin on a new version. Today the
  only way to pick up a new version is ``remove`` + ``install``.
- Per-plugin config loading. ``KernelContext.config`` is an empty
  mapping for now; config wiring lands with its own issue.
- The ``yaya plugin list / install / remove`` CLI. The registry exposes
  the underlying methods; the CLI adapter ships separately.
- 2.0 sandbox / capability restrictions. Plugins run as trusted
  in-process code per ``docs/dev/plugin-protocol.md``.
