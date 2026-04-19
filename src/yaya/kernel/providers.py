"""Provider-instance view over the live :class:`ConfigStore`.

Issue #116 introduces a flat ``providers.<id>.*`` namespace so one
``llm-provider`` plugin can back many configured *instances* — e.g.
one ``llm-openai`` plugin powering separate "OpenAI prod",
"Azure OpenAI", and "local-LM-Studio" records with distinct
``base_url`` / ``api_key`` / ``model`` fields. Until now the kernel
only understood a single active provider (the ``provider`` key) and
per-plugin settings lived under ``plugin.<name>.*``; that flat
namespace maxes out at one instance per plugin.

Schema::

    providers.<instance_id>.plugin    = "<plugin-name>"
    providers.<instance_id>.label     = "<human label>"
    providers.<instance_id>.<field>   = <value>   # arbitrary schema fields
    provider                          = "<instance_id>"  # active instance

:class:`ProvidersView` is a thin read surface over
:meth:`ConfigStore.list_prefix` — it groups keys by ``<instance_id>``,
separates meta fields (``plugin`` / ``label``) from user-facing config
fields, and surfaces the active instance id via :attr:`active_id`.
Writes stay on :class:`ConfigStore` — this view is read-only by
design (the CRUD surface lands with D4c's HTTP API).

Bootstrap lives in :class:`yaya.kernel.registry.PluginRegistry.start`
and runs exactly once per install: for every loaded ``llm-provider``
plugin it seeds ``providers.<plugin-name>.*`` from the legacy
``plugin.<plugin-name>.*`` sub-tree, stamps
``_meta.providers_seeded_at`` to prevent re-seeding, and defaults the
``provider`` key when unset. D4b then flips ``llm_openai`` /
``llm_echo`` / ``strategy_react`` to read instance-scoped config; this
module is the layer those reads ride on.

Layering: depends only on :mod:`yaya.kernel.config_store` and the
Python standard library. No imports from ``cli``, ``plugins``, or
``core``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type-only import, avoids a cycle.
    from yaya.kernel.config_store import ConfigStore

__all__ = [
    "PROVIDERS_PREFIX",
    "PROVIDERS_SEEDED_MARKER",
    "InstanceRow",
    "ProvidersView",
]


PROVIDERS_PREFIX = "providers."
"""Dotted prefix every provider-instance key shares.

Factored out so tests, the HTTP API, and the registry bootstrap code
agree on a single string without duplicating it at call sites.
"""

PROVIDERS_SEEDED_MARKER = "_meta.providers_seeded_at"
"""Key written once the first providers.* bootstrap completes.

Presence of this key (value irrelevant; epoch seconds by convention)
is the signal subsequent boots use to skip re-seeding. Same shape as
:data:`yaya.kernel.config_store._MIGRATION_MARKER` so operators have
one mental model for "have we run this one-time migration?".
"""

# Meta fields excluded from :attr:`InstanceRow.config`. They are part
# of the instance identity (``plugin``) or presentation (``label``) —
# not schema-level config that the backing plugin would consume.
_META_FIELDS = frozenset({"plugin", "label"})


@dataclass(frozen=True, slots=True)
class InstanceRow:
    """One provider-instance record parsed out of :class:`ConfigStore`.

    Attributes:
        id: Instance identifier — the ``<id>`` segment in
            ``providers.<id>.*``. Uniquely names this instance within
            the install.
        plugin: Name of the backing ``llm-provider`` plugin (e.g.
            ``"llm-openai"``). May be empty when a malformed subtree
            lacks the ``plugin`` meta field; callers that care should
            filter those out.
        label: Human-friendly display name. Defaults to the empty
            string when the ``label`` meta field is absent so consumers
            can fall back to ``id`` for display.
        config: Remaining keys under this instance, minus the meta
            fields. Plugin-facing schema fields like ``base_url`` /
            ``api_key`` / ``model`` land here.
    """

    id: str
    plugin: str
    label: str
    config: dict[str, Any]


class ProvidersView:
    """Read-only grouped view over ``providers.*`` keys in a :class:`ConfigStore`.

    The view is a thin adapter — every read re-parses the
    authoritative :class:`ConfigStore` cache so subsequent
    :meth:`ConfigStore.set` writes are visible without cache
    invalidation. Prefer constructing one view per request rather
    than caching the :class:`InstanceRow` objects across turns.

    Instances of :class:`ProvidersView` are cheap: no background
    tasks, no retained state besides the store reference.
    """

    def __init__(self, store: ConfigStore) -> None:
        """Bind the view to ``store``.

        Args:
            store: Live :class:`ConfigStore`. The view keeps a
                reference but does not own lifetime — the owning
                registry opens and closes the store.
        """
        self._store = store

    def list_instances(self) -> list[InstanceRow]:
        """Return every instance in deterministic ``id`` order.

        Instances whose keyspace is empty (e.g. a stray
        ``providers.foo.`` write with no dotted tail) are dropped —
        the grouping logic needs a field to attribute. Instances
        missing a ``plugin`` meta field are returned with
        ``plugin=""`` so operators can still see them and fix.

        Returns:
            A list of :class:`InstanceRow`, sorted by ``id``. Empty
            when the store has no ``providers.*`` rows.
        """
        grouped = self._grouped()
        return [self._row_from_group(instance_id, fields) for instance_id, fields in sorted(grouped.items())]

    def get_instance(self, instance_id: str) -> InstanceRow | None:
        """Return one instance by id, or ``None`` when absent.

        Args:
            instance_id: The ``<id>`` segment in ``providers.<id>.*``.

        Returns:
            The matching :class:`InstanceRow`, or ``None`` when no
            ``providers.<instance_id>.*`` keys exist.
        """
        grouped = self._grouped()
        fields = grouped.get(instance_id)
        if fields is None:
            return None
        return self._row_from_group(instance_id, fields)

    def instances_for_plugin(self, plugin_name: str) -> list[InstanceRow]:
        """Return every instance whose ``plugin`` meta equals ``plugin_name``.

        Args:
            plugin_name: Backing plugin name to filter by — matched
                byte-exact against the stored ``plugin`` meta field.

        Returns:
            A list of :class:`InstanceRow`, sorted by ``id``. Empty
            when no instances target ``plugin_name``.
        """
        return [row for row in self.list_instances() if row.plugin == plugin_name]

    @property
    def active_id(self) -> str | None:
        """Return the active instance id from the ``provider`` config key.

        Returns:
            The current ``provider`` value when it is a non-empty
            string, else ``None``. The getter does not assert the
            value resolves to an existing instance — callers decide
            whether to treat "active id with no matching row" as an
            error or fall back to a default.
        """
        cache = self._store_cache()
        value = cache.get("provider")
        if isinstance(value, str) and value:
            return value
        return None

    # -- internals ------------------------------------------------------------

    def _store_cache(self) -> dict[str, Any]:
        """Return the backing :class:`ConfigStore`'s live cache dict.

        Accessing the private ``_cache`` attribute is intentional:
        the store exposes async :meth:`ConfigStore.list_prefix` /
        :meth:`ConfigStore.get` for plugin code, but this view lives
        in the kernel and needs a synchronous surface so
        :class:`KernelContext` can expose ``ctx.providers`` without
        forcing every read through an ``await``. The store itself
        keeps cache / DB in lock-step on every write (lesson: cache
        is authoritative), so reading the cache directly here is
        safe.
        """
        # Exposed as a protected attribute — ConfigStore owns the
        # dict and mutates it under the same executor-serialised
        # write that hits sqlite, so reads are race-free at the
        # single-loop level.
        return self._store._cache  # pyright: ignore[reportPrivateUsage]

    def _grouped(self) -> dict[str, dict[str, Any]]:
        """Group every ``providers.<id>.<field>`` key by ``<id>``.

        Keys that do not fit the ``providers.<id>.<field>`` shape
        (e.g. a malformed ``providers.foo`` with no trailing field)
        are silently dropped — the grouping needs a field to
        attribute, and :class:`ConfigStore` already validates keys at
        write time.
        """
        cache = self._store_cache()
        out: dict[str, dict[str, Any]] = {}
        for key, value in cache.items():
            if not key.startswith(PROVIDERS_PREFIX):
                continue
            rest = key[len(PROVIDERS_PREFIX) :]
            if "." not in rest:
                # ``providers.foo`` with no trailing field — ignore.
                continue
            instance_id, field = rest.split(".", 1)
            if not instance_id or not field:
                continue
            out.setdefault(instance_id, {})[field] = value
        return out

    @staticmethod
    def _row_from_group(instance_id: str, fields: dict[str, Any]) -> InstanceRow:
        """Split ``fields`` into meta + config and return an :class:`InstanceRow`."""
        plugin_raw = fields.get("plugin", "")
        label_raw = fields.get("label", "")
        plugin = plugin_raw if isinstance(plugin_raw, str) else ""
        label = label_raw if isinstance(label_raw, str) else ""
        config = {k: v for k, v in fields.items() if k not in _META_FIELDS}
        return InstanceRow(id=instance_id, plugin=plugin, label=label, config=config)
