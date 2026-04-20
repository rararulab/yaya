spec: task
name: "instance-dispatch"
tags: [plugin, llm-provider, strategy, providers]
---

## Intent

D4a (#116) reserved the ``providers.<id>.*`` namespace and a
``ProvidersView`` read surface so one ``llm-provider`` plugin can
back many configured instances. The plugins themselves still read
from the legacy ``plugin.<ns>.*`` sub-tree and dispatched off a
single ``provider == "<plugin-name>"`` match, which defeated the
whole point: an operator could configure two "OpenAI prod" and
"Azure OpenAI" instances and only one would ever answer.

This spec flips the three affected bundled plugins (``llm_openai``,
``llm_echo``, ``strategy_react``) to **instance-scoped dispatch**.
Each ``llm-provider`` plugin maintains per-instance state keyed by
the id under ``providers.<id>.*`` whose ``plugin`` meta names that
plugin; ``strategy_react`` resolves the active instance via
``ctx.providers.active_id`` and reads ``model`` from the instance's
own config. ``config.updated`` drives per-instance hot-reload — add
/ drop / rebuild one client without restarting the kernel.

## Decisions

- ``llm_openai`` keeps a ``self._clients: dict[str, AsyncOpenAI]``
  keyed by instance id, populated from
  ``ctx.providers.instances_for_plugin(self.name)`` on load. Dispatch
  matches ``ev.payload["provider"]`` against the dict; non-matching
  ids return silently so sibling ``llm-provider`` plugins coexist.
- ``llm_echo`` keeps a ``self._active_instances: set[str]`` with
  the same ownership semantics. No per-instance state beyond
  membership — echo is stateless.
- ``strategy_react._provider_and_model`` reads
  ``ctx.providers.active_id`` and resolves model via
  ``ctx.providers.get_instance(active_id).config["model"]``. Fallback
  order when ``ctx.providers`` is absent: env sniff over
  ``OPENAI_API_KEY`` → ``llm-openai`` / ``gpt-4o-mini``, else
  ``llm-echo`` / ``echo``. Fallback instance names mirror the
  D4a-seeded ids so production and fallback resolve to identical
  strings.
- Hot-reload path for both LLM-provider plugins: on
  ``config.updated`` with a ``providers.<id>.*`` key, look up the
  instance via ``ctx.providers.get_instance(id)``; if absent or no
  longer owned, drop the per-instance state; otherwise rebuild it.
  Unrelated prefixes (e.g. ``plugin.other.thing``) are ignored.
- ``llm_openai`` never ``await``s the old client's ``close()`` when
  rebuilding — pool reclamation rides GC so in-flight dispatches
  finish cleanly (preserves the #106 fix).
- Legacy ``plugin.<ns>.*`` reads are removed from all three plugins.
  D4a's bootstrap lift guarantees pre-existing keys survive as
  ``providers.<plugin-name>.*`` rows on first upgrade.

## Boundaries

### Allowed Changes
- src/yaya/plugins/llm_echo/plugin.py
- src/yaya/plugins/llm_echo/__init__.py
- src/yaya/plugins/llm_echo/AGENT.md
- src/yaya/plugins/llm_openai/plugin.py
- src/yaya/plugins/llm_openai/__init__.py
- src/yaya/plugins/llm_openai/AGENT.md
- src/yaya/plugins/strategy_react/plugin.py
- src/yaya/plugins/strategy_react/AGENT.md
- tests/plugins/llm_echo/test_echo.py
- tests/plugins/llm_openai/test_llm_openai.py
- tests/plugins/strategy_react/test_strategy_react.py
- tests/plugins/strategy_react/test_provider_selection.py
- tests/plugins/agent_tool/test_agent_tool_integration.py
- tests/kernel/test_strategy_hot_provider.py
- tests/bdd/test_plugins.py
- tests/bdd/features/plugin-llm_openai.feature
- tests/bdd/features/kernel-config-store.feature
- tests/bdd/features/instance-dispatch.feature
- specs/instance-dispatch.spec
- specs/plugin-llm_openai.spec
- specs/kernel-config-store.spec
- docs/dev/plugin-protocol.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/web/
- src/yaya/plugins/memory_sqlite/
- src/yaya/plugins/tool_bash/
- src/yaya/plugins/agent_tool/
- src/yaya/plugins/mcp_bridge/
- GOAL.md
- pyproject.toml

## Completion Criteria

Scenario: AC-01 llm_openai builds one client per owned instance on load
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_two_instances_route_to_distinct_clients
  Level: unit
  Given a ConfigStore with two providers.<id> rows whose plugin meta equals llm-openai
  When the plugin on_load runs against a KernelContext bound to that store
  Then self._clients contains one AsyncOpenAI per instance id and unrelated instances are skipped

Scenario: AC-02 llm_openai dispatch filters by instance id against the owned-client dict
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_non_matching_provider_is_ignored
  Level: unit
  Given an llm_openai plugin holding a stub client under instance id prod
  When a llm.call.request payload provider equals an unowned id
  Then the plugin emits no llm.call.response and no llm.call.error

Scenario: AC-03 llm_echo answers only for owned instance ids
  Test:
    Package: yaya
    Filter: tests/plugins/llm_echo/test_echo.py::test_non_matching_provider_is_ignored
  Level: unit
  Given a llm_echo plugin with active instance set containing llm-echo
  When a llm.call.request for a non-owned provider id is published
  Then no llm.call.response event is emitted by the llm-echo plugin

Scenario: AC-04 strategy_react resolves model from the active instance config
  Test:
    Package: yaya
    Filter: tests/plugins/strategy_react/test_strategy_react.py::test_provider_and_model_reads_instance_config
  Level: unit
  Given a ConfigStore with provider set to instance-a whose config model field equals gpt-4.1
  When strategy_react handles a strategy.decide.request
  Then the emitted strategy.decide.response carries provider instance-a and model gpt-4.1

Scenario: AC-05 llm_openai rebuilds only the edited instance on providers.<id> config.updated
  Test:
    Package: yaya
    Filter: tests/plugins/llm_openai/test_llm_openai.py::test_config_updated_rebuilds_only_affected_instance
  Level: unit
  Given an llm_openai plugin with two seeded instances prod and azure
  When providers.prod.base_url is updated and config.updated is delivered
  Then only the prod client is rebuilt and the azure client instance remains the same object

## Out of Scope

- CRUD HTTP surface for ``providers.<id>.*`` — lands in D4c.
- UI affordances for switching the active instance — lands in D4d.
- Garbage-collecting the legacy ``plugin.<ns>.*`` rows — follow-up
  PR after D4b lands and confirms no plugin still reads them.
- Per-plugin schema validation beyond the existing ``api_key`` /
  ``base_url`` / ``model`` reads.
