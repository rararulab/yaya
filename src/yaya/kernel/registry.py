"""Plugin registry: entry-point discovery, lifecycle, and failure isolation.

The registry is the kernel's inhabitant-management layer. It discovers
plugins via the :pep:`621` / setuptools entry-point group
``yaya.plugins.v1``, instantiates each declared :class:`~yaya.kernel.plugin.Plugin`
object, wires its :meth:`~yaya.kernel.plugin.Plugin.subscriptions` into the
bus, drives ``on_load`` / ``on_event`` / ``on_unload``, and isolates
repeat-offender plugins by unsubscribing and emitting ``plugin.removed``
once a configurable failure threshold is breached.

**One code path.** Bundled and third-party plugins go through the exact
same registration logic; "bundled" is only a deterministic load-order
tie-breaker and never a behavioral branch. This mirrors the product
principle in ``GOAL.md`` (no special cases for bundled plugins).

**Failure accounting.** The registry subscribes to ``plugin.error`` with
``source="kernel-registry"`` (not ``"kernel"``, which is reserved for the
bus's synthetic-error path and trips the recursion guard in
``bus._report_handler_failure``). When a ``plugin.error`` arrives, the
handler increments the offending plugin's counter; a **successful**
``on_event`` invocation resets that counter to zero, so the threshold
counts **consecutive** failures, not cumulative. Once the counter
breaches the configured threshold the registry flips the record's
status to ``UNLOADING`` **synchronously** (before returning from the
error handler) and THEN spawns the unload task via
``asyncio.create_task(..., context=contextvars.Context())`` so the
bus's private ``_IN_WORKER`` ContextVar resets — without that reset,
the unload task's ``await bus.publish(...)`` would fire-and-forget and
``plugin.removed`` would never reach adapters. The synchronous status
flip matters because 10 concurrent ``plugin.error`` events (one per
session) for the same plugin past threshold would otherwise each see
``status is LOADED`` and spawn parallel unload tasks. Status ladder:
``loaded → unloading → failed`` for the threshold path vs
``loaded → unloaded`` for orderly ``stop()`` / ``remove()``. Same
context-reset pattern the agent loop uses for per-turn tasks.

**install / remove**. Shells to ``uv pip`` via
:func:`asyncio.create_subprocess_exec` (no ``shell=True``). Falls back to
plain ``pip`` when ``uv`` is not on ``PATH``. After a successful install
or uninstall, discovery runs again so freshly installed plugins come
online (and removed ones drop out of the snapshot) without restarting
the kernel.

Layering: only imports :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
and :mod:`yaya.kernel.plugin` plus the Python standard library. No
imports from ``cli``, ``plugins``, ``core``, or ``loop``.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import EntryPoint, PackageNotFoundError, distribution, entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from yaya.kernel.approval import install_approval_runtime, uninstall_approval_runtime
from yaya.kernel.config import KernelConfig, flatten_kernel_config, load_config
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.logging import get_plugin_logger
from yaya.kernel.plugin import Category, KernelContext, Plugin
from yaya.kernel.session import Session

if TYPE_CHECKING:  # pragma: no cover - type-only import, breaks an import cycle.
    from yaya.kernel.bus import EventBus, Subscription

_logger = logging.getLogger(__name__)

_SOURCE = "kernel-registry"
"""Subscription source for registry-owned handlers.

Deliberately NOT ``"kernel"``: the bus short-circuits its
``plugin.error`` re-emission when a handler with ``source="kernel"``
raises (bus recursion guard). We want registry-handler failures to
still be observable, so we claim a distinct source.
"""

_ENTRY_POINT_GROUP = "yaya.plugins.v1"
"""Public entry-point group per ``docs/dev/plugin-protocol.md``."""

_DEFAULT_FAILURE_THRESHOLD = 3
"""Consecutive ``plugin.error`` events tolerated before unload.

A successful :meth:`Plugin.on_event` invocation resets the counter to
zero, so N *consecutive* failures — not N cumulative — trigger unload.
"""

# Yaya's own distribution name — used to tag bundled plugins so the
# registry can (a) order them first deterministically and (b) reject
# ``remove("<bundled>")`` with a clear ``ValueError``.
_YAYA_DIST = "yaya"


class PluginStatus(StrEnum):
    """Lifecycle status of a registered plugin.

    Reported verbatim by :meth:`PluginRegistry.snapshot` and shown in
    ``yaya plugin list``. The set is closed; adding a new value is a
    contract change.

    Status ladder:

    * ``loaded`` — plugin registered and accepting events.
    * ``unloading`` — **transient**: threshold breached, unload task
      scheduled but ``on_unload`` has not yet completed. Surfaced so
      operators see in-flight unloads; rival ``plugin.error`` events
      for the same plugin observe this state and do NOT schedule a
      duplicate unload task.
    * ``failed`` — terminal, threshold path (``loaded → unloading →
      failed``) or a discovery-time failure (bad entry point, invalid
      object, ``on_load`` raised).
    * ``unloaded`` — terminal, orderly shutdown (``loaded → unloaded``
      via ``stop()`` or ``remove()``).
    """

    LOADED = "loaded"
    UNLOADING = "unloading"
    FAILED = "failed"
    UNLOADED = "unloaded"


@dataclass(slots=True, eq=False)
class _PluginRecord:
    """Internal bookkeeping for one registered plugin.

    ``eq=False`` so this dataclass falls back to identity equality. The
    :attr:`subs` list holds :class:`~yaya.kernel.bus.Subscription`
    handles that themselves use identity semantics (lesson #7); keeping
    the owning record identity-keyed avoids the same hazard one layer
    up when we look a record up by plugin name.
    """

    plugin: Plugin
    ctx: KernelContext
    subs: list[Subscription]
    status: PluginStatus
    bundled: bool = False
    error_count: int = 0


class PluginRegistry:
    """Discover, load, and supervise plugins for a running kernel.

    The registry is tied to one :class:`~yaya.kernel.bus.EventBus`
    instance in one asyncio loop. Instantiate it after the bus is ready;
    call :meth:`start` to discover and load plugins, :meth:`stop` to
    unload them in reverse order on shutdown. Between the two, the
    registry reacts to ``plugin.error`` events via an internal handler
    and unloads plugins whose consecutive-error count breaches the
    threshold.

    Example:
        ::

            bus = EventBus()
            registry = PluginRegistry(bus)
            await registry.start()
            # ... kernel runs ...
            await registry.stop()

    Thread model: single asyncio loop. Not safe to share across loops.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        state_dir: Path | None = None,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        entry_point_group: str = _ENTRY_POINT_GROUP,
        kernel_config: KernelConfig | None = None,
        session: Session | None = None,
        config_store: ConfigStore | None = None,
        config_db_path: Path | None = None,
    ) -> None:
        """Bind the registry to ``bus``.

        Args:
            bus: The running kernel event bus. Subscriptions are created
                during :meth:`start` so callers can wire fixtures first.
            state_dir: Parent directory for per-plugin state dirs. Each
                plugin gets ``<state_dir>/<plugin.name>/``. Defaults to
                ``<XDG_DATA_HOME>/yaya/plugins/`` (or
                ``~/.local/share/yaya/plugins/``).
            failure_threshold: Consecutive ``plugin.error`` events this
                registry tolerates before unloading the offending
                plugin. Default 3 per the protocol doc.
            entry_point_group: Override for tests that need to inject a
                separate entry-point group. Production code should use
                the default.
            kernel_config: Resolved :class:`KernelConfig`. Defaults to
                ``load_config()`` so production callers (``yaya serve``)
                pick up env + TOML automatically; tests can inject a
                hand-built instance to pin per-plugin sub-trees.
            config_store: Pre-opened :class:`ConfigStore`. Production
                callers leave this unset — :meth:`start` opens one
                under :func:`yaya.kernel.config_store.default_config_db_path`
                and the registry owns its lifetime. Tests inject a
                store wired to an in-tmp path.
            config_db_path: Override for the config DB location when
                ``config_store`` is not supplied. Defaults to
                :func:`yaya.kernel.config_store.default_config_db_path`.
        """
        self._bus = bus
        self._state_dir = state_dir or _default_state_dir()
        self._failure_threshold = failure_threshold
        self._entry_point_group = entry_point_group
        self._kernel_config = kernel_config or load_config()
        self._session = session
        # When a caller supplies a ConfigStore, the registry does not
        # open/close it — the caller owns lifetime. When absent,
        # :meth:`start` opens one and :meth:`stop` closes it.
        self._config_store: ConfigStore | None = config_store
        self._config_db_path = config_db_path
        self._owns_config_store = config_store is None

        # Name → record. Bounded by the install set (not user input), so
        # there is no leak risk even across many discovery cycles.
        self._records: dict[str, _PluginRecord] = {}
        # Load order — needed for reverse-order unload on stop().
        self._load_order: list[str] = []
        # Names known to be bundled — authoritative entry-point-derived
        # guard for ``remove()``. Populated during discovery with BOTH
        # ``ep.name`` and (if load succeeded) ``plugin.name`` so
        # ``remove()`` blocks bundled packages even when their load
        # failed or discovery hasn't reached them yet.
        self._bundled_names: set[str] = set()
        # Subscription for our own ``plugin.error`` accounting handler.
        self._error_sub: Subscription | None = None
        # Pending unload tasks so stop() can await them (no leak on shutdown).
        self._unload_tasks: set[asyncio.Task[None]] = set()
        # Entry-point names we've already warned about for missing dist
        # metadata. Keeps ``_is_ep_bundled`` idempotent across repeated
        # discovery passes instead of spamming the log.
        self._warned_no_dist: set[str] = set()
        self._started = False

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to ``plugin.error``, then discover + load every plugin.

        Emits one ``plugin.loaded`` event per successful load and a final
        ``kernel.ready`` event once the full first-pass discovery has
        finished. Idempotent: a second call is a no-op so test fixtures
        composing multiple layers can call it defensively.
        """
        if self._started:
            return
        self._started = True

        # Open the live config store BEFORE plugin discovery so every
        # ``on_load`` sees a populated :class:`ConfigView`. On first
        # boot of a fresh install the DB is empty and we seed it from
        # the legacy TOML + env config; after migration the DB is
        # source of truth and subsequent boots skip this step.
        if self._config_store is None:
            self._config_store = await ConfigStore.open(
                bus=self._bus,
                path=self._config_db_path,
            )
            flat = flatten_kernel_config(self._kernel_config)
            await self._config_store.migrate_from_kernel_config(flat)

        self._error_sub = self._bus.subscribe(
            "plugin.error",
            self._on_plugin_error,
            source=_SOURCE,
        )

        await self._discover_and_load()

        # The approval runtime sits between the dispatcher and adapters.
        # Install it AFTER plugins (so adapters are already subscribed
        # to ``approval.request`` and cannot miss the first prompt) but
        # BEFORE ``kernel.ready`` (so the first ``tool.call.request`` that
        # reaches a ``requires_approval=True`` tool finds the runtime
        # already subscribed).
        await install_approval_runtime(self._bus)

        if not self._load_order:
            _logger.info(
                "kernel boot: no plugins discovered in entry-point group %r; "
                "kernel.ready will emit but the system cannot service requests",
                self._entry_point_group,
            )

        await self._bus.publish(
            new_event(
                "kernel.ready",
                {"version": _yaya_version()},
                session_id="kernel",
                source="kernel",
            )
        )

    async def stop(self) -> None:
        """Unload every plugin in reverse load order; emit ``kernel.shutdown``.

        Unsubscribes the registry's ``plugin.error`` handler, waits for
        any in-flight unload tasks spawned by the failure-accounting
        path, then runs each loaded plugin's ``on_unload`` inside a
        ``try/except`` so one noisy plugin cannot block the rest.
        """
        if not self._started:
            return
        self._started = False

        if self._error_sub is not None:
            self._error_sub.unsubscribe()
            self._error_sub = None

        # Drain any in-flight unload tasks spawned from the error handler
        # before we iterate self._records for the orderly-shutdown pass.
        if self._unload_tasks:
            await asyncio.gather(*self._unload_tasks, return_exceptions=True)
            self._unload_tasks.clear()

        for name in reversed(self._load_order):
            record = self._records.get(name)
            if record is None or record.status is not PluginStatus.LOADED:
                continue
            await self._unload_record(record, emit_removed=False)

        # Mirror the start-path install. Runs BEFORE ``kernel.shutdown``
        # so pending approval futures observe
        # :class:`ApprovalCancelledError(reason="shutdown")` instead of
        # hanging on the per-request timeout as the loop tears down.
        await uninstall_approval_runtime(self._bus)

        await self._bus.publish(
            new_event(
                "kernel.shutdown",
                {"reason": "stop"},
                session_id="kernel",
                source="kernel",
            )
        )

        # Close the ConfigStore AFTER every plugin's ``on_unload`` and
        # after ``kernel.shutdown`` fans out, so adapters observing
        # teardown events still see ``ctx.config`` — some rely on it
        # during their own shutdown rendering. Only close stores the
        # registry owns; caller-supplied stores stay open.
        if self._owns_config_store and self._config_store is not None:
            await self._config_store.close()
            self._config_store = None

    # -- introspection ----------------------------------------------------------

    def snapshot(self) -> list[dict[str, str]]:
        """Return a point-in-time view of every known plugin.

        Returns:
            A list of ``{"name", "version", "category", "status"}`` dicts
            — one entry per plugin that discovery has ever seen during
            this registry's lifetime. Ordering matches first-seen load
            order so ``yaya plugin list`` output is deterministic.
        """
        rows: list[dict[str, str]] = []
        for name in self._load_order:
            record = self._records.get(name)
            if record is None:
                continue
            rows.append({
                "name": record.plugin.name,
                "version": record.plugin.version,
                "category": str(record.plugin.category),
                "status": str(record.status),
            })
        return rows

    def loaded_plugins(self, category: Category | None = None) -> list[Plugin]:
        """Return live plugin instances, optionally filtered by ``category``.

        Unlike :meth:`snapshot` (which returns string rows for UI /
        JSON rendering), this surface exposes the concrete ``Plugin``
        objects so kernel-side subsystems can introspect them — e.g.
        the ``yaya serve`` boot path needs a live
        :class:`~yaya.kernel.llm.LLMProvider` to wire the compaction
        manager (#93). Only plugins currently in
        :data:`PluginStatus.LOADED` are returned, in load order.

        Args:
            category: When set, restrict to this category; when
                ``None``, return every loaded plugin.

        Returns:
            A list of :class:`~yaya.kernel.plugin.Plugin` instances,
            empty when no plugin matches.
        """
        out: list[Plugin] = []
        for name in self._load_order:
            record = self._records.get(name)
            if record is None or record.status is not PluginStatus.LOADED:
                continue
            if category is not None and record.plugin.category is not category:
                continue
            out.append(record.plugin)
        return out

    # -- install / remove -------------------------------------------------------

    async def install(self, source: str, *, editable: bool = False) -> str:
        """Install a plugin package and refresh discovery.

        Args:
            source: PyPI distribution name, absolute path, ``file://``
                URL, or ``https://`` URL. Anything else is rejected.
            editable: If true, pass ``-e`` for dev-mode installs (path /
                ``file://`` sources only; PyPI names ignore this).

        Returns:
            The ``source`` argument, echoed back. Resolving to an actual
            distribution name requires parsing ``uv pip`` output which is
            format-unstable; callers wanting the dist name should consult
            :meth:`snapshot` after install completes.

        Raises:
            ValueError: If ``source`` does not look like a PyPI name /
                path / URL.
            RuntimeError: If the subprocess returned non-zero. The stderr
                from pip is included in the message.
        """
        validate_install_source(source)
        args = ["pip", "install"]
        if editable:
            args.append("-e")
        args.append(source)
        await _run_package_command(args)
        await self._discover_and_load()
        return source

    async def remove(self, name: str) -> None:
        """Uninstall a plugin package and refresh discovery.

        Args:
            name: Plugin name (kebab-case) to uninstall.

        Raises:
            ValueError: If ``name`` is a bundled plugin — bundled
                plugins ship inside yaya's own wheel and cannot be
                uninstalled independently. Use ``yaya update`` to move
                to a build without the bundled plugin.
            RuntimeError: If the subprocess returned non-zero.
        """
        # Refresh the bundled set from entry-point metadata so third-party
        # plugins installed just before this call still get the correct
        # guard decision even if earlier discovery failed to load them.
        self._refresh_bundled_names()
        if name in self._bundled_names:
            raise ValueError(f"cannot remove bundled plugin {name!r}")

        record = self._records.get(name)

        await _run_package_command(["pip", "uninstall", "-y", name])

        # After uninstall, unload any in-memory record the discovery
        # cycle won't revisit (its entry point is gone).
        if record is not None and record.status is PluginStatus.LOADED:
            await self._unload_record(record, emit_removed=True)

        # Prune the record — the entry point is gone, so
        # ``_discover_and_load`` below will not re-visit it. Leaving the
        # stale row in ``_records`` / ``_load_order`` would keep
        # ``snapshot()`` reporting an uninstalled plugin (and block a
        # subsequent re-install from running ``on_load`` because the
        # name still collides in ``_load_entry_point``'s dedup check).
        # Runs AFTER the bundled guard above, so bundled plugins are
        # never pruned here.
        self._records.pop(name, None)
        if name in self._load_order:
            self._load_order.remove(name)
        self._bundled_names.discard(name)

        await self._discover_and_load()

    # -- discovery --------------------------------------------------------------

    async def _discover_and_load(self) -> None:
        """Enumerate entry points, load anything new.

        Bundled plugins sort first; third-party second. Within each group
        entries are sorted by entry-point name for deterministic order.
        A plugin already present in :attr:`_records` is skipped (the
        registry does not currently hot-reload; a new version needs
        explicit ``remove`` + ``install``).
        """
        eps = list(entry_points(group=self._entry_point_group))
        # Partition bundled vs third-party for deterministic ordering.
        bundled: list[EntryPoint] = []
        third_party: list[EntryPoint] = []
        for ep in eps:
            if self._is_ep_bundled(ep):
                bundled.append(ep)
                # Record ``ep.name`` now so ``remove()`` blocks bundled
                # packages even if the load fails below.
                self._bundled_names.add(ep.name)
            else:
                third_party.append(ep)
        bundled.sort(key=lambda e: e.name)
        third_party.sort(key=lambda e: e.name)

        for ep in (*bundled, *third_party):
            await self._load_entry_point(ep, bundled=self._is_ep_bundled(ep))

    def _refresh_bundled_names(self) -> None:
        """Populate :attr:`_bundled_names` from current entry-point metadata.

        Used by :meth:`remove` so the bundled guard does not depend on
        an earlier :meth:`_discover_and_load` having succeeded. Idempotent —
        the underlying set dedups.
        """
        for ep in entry_points(group=self._entry_point_group):
            if self._is_ep_bundled(ep):
                self._bundled_names.add(ep.name)

    async def _load_entry_point(self, ep: EntryPoint, *, bundled: bool) -> None:
        """Resolve one entry point and register the plugin it exposes.

        Any failure path — import error, non-conforming object, or
        ``on_load`` raising — emits a ``plugin.error`` event and marks
        the record status ``failed``. The registry does not crash.
        """
        try:
            obj = ep.load()
        except Exception as exc:
            _logger.warning("failed to load entry point %r: %s", ep.name, exc)
            await self._emit_plugin_error(ep.name, f"entry_point_load_failed: {exc}")
            return

        if not isinstance(obj, Plugin):
            _logger.warning(
                "entry point %r resolved to a non-Plugin object (%r); skipping",
                ep.name,
                type(obj).__name__,
            )
            await self._emit_plugin_error(ep.name, "invalid_plugin_object")
            return

        plugin: Plugin = obj
        name = plugin.name
        if bundled:
            # Tag the plugin's declared name too; ``remove(plugin.name)``
            # must still be blocked for bundled plugins even if the
            # entry-point name differs.
            self._bundled_names.add(name)
        if name in self._records:
            # Already loaded (or already in a terminal state) — skip.
            return

        ctx = self._make_context(plugin)
        try:
            await plugin.on_load(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("plugin %r raised in on_load: %s", name, exc)
            self._register_record(
                _PluginRecord(
                    plugin=plugin,
                    ctx=ctx,
                    subs=[],
                    status=PluginStatus.FAILED,
                    bundled=bundled,
                )
            )
            await self._emit_plugin_error(name, f"on_load_failed: {exc}")
            return

        def _reset_counter(_name: str = name) -> None:
            rec = self._records.get(_name)
            if rec is not None and rec.status is PluginStatus.LOADED:
                rec.error_count = 0

        handler = _make_handler(plugin, ctx, on_success=_reset_counter)
        subs: list[Subscription] = [
            self._bus.subscribe(kind, handler, source=name) for kind in _safe_subscriptions(plugin)
        ]
        self._register_record(
            _PluginRecord(
                plugin=plugin,
                ctx=ctx,
                subs=subs,
                status=PluginStatus.LOADED,
                bundled=bundled,
            )
        )

        await self._bus.publish(
            new_event(
                "plugin.loaded",
                {
                    "name": name,
                    "version": plugin.version,
                    "category": _category_str(plugin.category),
                },
                session_id="kernel",
                source="kernel",
            )
        )

    def _register_record(self, record: _PluginRecord) -> None:
        """Install ``record`` into the registry's bookkeeping.

        Single-source the ``_records`` + ``_load_order`` write so both
        branches of :meth:`_load_entry_point` (FAILED path on ``on_load``
        raising, LOADED path on success) stay in lock-step for future
        audits.
        """
        self._records[record.plugin.name] = record
        self._load_order.append(record.plugin.name)

    def _is_ep_bundled(self, ep: EntryPoint) -> bool:
        """Return True if ``ep`` ships inside yaya's own distribution.

        Logs a WARNING at most once per entry-point name when ``ep.dist``
        is None so repeated discovery passes (install / remove re-runs
        discovery) don't spam the log with the same "no distribution
        metadata" message.
        """
        dist = ep.dist
        if dist is None:
            if ep.name not in self._warned_no_dist:
                _logger.warning(
                    "entry point %r has no distribution metadata; treating as third-party",
                    ep.name,
                )
                self._warned_no_dist.add(ep.name)
            return False
        # ``metadata['Name']`` is normalised lower-case in Python 3.10+.
        return (dist.metadata["Name"] or "").lower() == _YAYA_DIST

    def _make_context(self, plugin: Plugin) -> KernelContext:
        """Build the :class:`KernelContext` handed to ``plugin``'s lifecycle hooks."""
        plugin_state = self._state_dir / plugin.name
        plugin_state.mkdir(parents=True, exist_ok=True)
        config: Mapping[str, Any]
        if self._config_store is not None:
            # Per-plugin scoped view over the live store. Plugin name
            # is normalised to underscores so the legacy ``extras``
            # namespace (``llm_openai``) and the kebab-case plugin
            # name (``llm-openai``) both land on the same flattened
            # prefix ``plugin.llm_openai.``.
            ns = plugin.name.replace("-", "_")
            config = self._config_store.view(prefix=f"plugin.{ns}.")
        else:
            # Fallback for callers that skip :meth:`start` (tests,
            # ``yaya plugin list`` transient path).
            config = self._kernel_config.plugin_config(plugin.name)
        return KernelContext(
            bus=self._bus,
            logger=get_plugin_logger(plugin.name),
            config=config,
            state_dir=plugin_state,
            plugin_name=plugin.name,
            session=self._session,
        )

    # -- failure accounting -----------------------------------------------------

    async def _on_plugin_error(self, ev: Event) -> None:
        """Count consecutive ``plugin.error`` events and trigger unloads.

        Runs **inside a bus session worker** (see ``bus.py::_drain``).
        The bus sets its ``_IN_WORKER`` ContextVar to True while this
        handler executes, so any nested ``bus.publish`` would fire-and-
        forget. The actual unload — which calls ``bus.publish`` for
        ``plugin.removed`` and awaits the plugin's ``on_unload`` — is
        spawned via :func:`asyncio.create_task` with an **empty**
        :class:`contextvars.Context` so ``_IN_WORKER`` resets to False
        inside the spawned task (same pattern as
        :class:`yaya.kernel.loop.AgentLoop._on_user_message`). This is
        the only way the unload task's ``plugin.removed`` publish
        actually reaches subscribers.
        """
        name = ev.payload.get("name")
        if not isinstance(name, str):
            _logger.warning(
                "plugin.error without 'name' payload; cannot attribute (source=%r, payload=%r)",
                ev.source,
                ev.payload,
            )
            return
        record = self._records.get(name)
        if record is None or record.status is not PluginStatus.LOADED:
            return
        record.error_count += 1
        if record.error_count < self._failure_threshold:
            return

        # Claim the unload synchronously: flip status BEFORE create_task
        # returns so rival ``plugin.error`` events for the same plugin —
        # arriving from other sessions while this handler is still
        # scheduling the unload task — fall through the ``status is not
        # LOADED`` guard above instead of spawning duplicate unload
        # tasks. Without this flip, 10 concurrent sessions each pushing
        # the same plugin past threshold would fire ``on_unload`` up to
        # 10 times and interleave ``plugin.removed`` emissions.
        record.status = PluginStatus.UNLOADING

        # Threshold breached — spawn the unload task in a fresh context
        # so ``_IN_WORKER`` resets and the task's publishes await delivery.
        ctx = contextvars.Context()
        task = asyncio.get_running_loop().create_task(
            self._unload_record(record, emit_removed=True, reason="threshold"),
            name=f"yaya-registry-unload:{name}",
            context=ctx,
        )
        self._unload_tasks.add(task)
        task.add_done_callback(self._unload_tasks.discard)

    async def _unload_record(
        self,
        record: _PluginRecord,
        *,
        emit_removed: bool,
        reason: str = "stop",
    ) -> None:
        """Unsubscribe, run ``on_unload``, and optionally emit ``plugin.removed``.

        ``on_unload`` exceptions are logged and swallowed — once we have
        decided to unload a plugin, propagating its cleanup error would
        just replace a known-bad plugin with a wedged registry.

        Args:
            record: The plugin record to unload.
            emit_removed: If True, publish a ``plugin.removed`` event.
            reason: Why the unload is happening. ``"threshold"`` is the
                failure-accounting path and ends in status ``failed``;
                every other value (default ``"stop"``, also used by
                ``remove()``) ends in status ``unloaded`` regardless of
                the plugin's lingering ``error_count``.
        """
        for sub in record.subs:
            sub.unsubscribe()
        record.subs.clear()

        try:
            await record.plugin.on_unload(record.ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning(
                "plugin %r raised in on_unload; continuing: %s",
                record.plugin.name,
                exc,
            )

        record.status = PluginStatus.FAILED if reason == "threshold" else PluginStatus.UNLOADED

        if emit_removed:
            await self._bus.publish(
                new_event(
                    "plugin.removed",
                    {"name": record.plugin.name},
                    session_id="kernel",
                    source="kernel",
                )
            )

    async def _emit_plugin_error(self, name: str, error: str) -> None:
        """Publish a ``plugin.error`` from the registry.

        Used for discovery-time failures (entry-point import, non-Plugin
        object, ``on_load`` raising) where the bus has no subscriber to
        synthesize an error on our behalf.
        """
        await self._bus.publish(
            new_event(
                "plugin.error",
                {"name": name, "error": error},
                session_id="kernel",
                source="kernel",
            )
        )


# ---------------------------------------------------------------------------
# Module-level helpers.
# ---------------------------------------------------------------------------


def _default_state_dir() -> Path:
    """Return ``<XDG_DATA_HOME>/yaya/plugins/`` (or the ~/.local/share fallback)."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "yaya" / "plugins"


def _yaya_version() -> str:
    """Best-effort yaya package version for ``kernel.ready`` payloads."""
    try:
        return distribution(_YAYA_DIST).version
    except PackageNotFoundError:
        return "0.0.0"


def _category_str(category: Any) -> str:
    """Coerce a :class:`Category` (or look-alike) to its string value."""
    if isinstance(category, Category):
        return category.value
    return str(category)


def _safe_subscriptions(plugin: Plugin) -> list[str]:
    """Call ``plugin.subscriptions()`` defensively; fall back to ``[]``.

    A misbehaving plugin that raises from ``subscriptions()`` still
    counts as loaded (``on_load`` already succeeded), but contributes no
    subscriptions. A ``plugin.error`` is emitted so the incident is
    observable.
    """
    try:
        subs = plugin.subscriptions()
    except Exception as exc:
        _logger.warning("plugin %r raised in subscriptions(): %s", plugin.name, exc)
        return []
    return list(subs)


def validate_install_source(source: str) -> None:
    """Reject install sources that do not match an accepted shape.

    Accepted forms:

    * PyPI distribution name — ``[a-zA-Z0-9][a-zA-Z0-9_.-]*`` (with
      optional version specifier like ``foo==1.2.3``).
    * Absolute filesystem path (``/foo/bar`` or ``C:\\foo``).
    * ``file://`` URL.
    * ``https://`` URL.

    Everything else — git URLs (not yet supported), relative paths,
    plain ``http://`` — is rejected with :class:`ValueError`.

    Shell-injection safety comes from :func:`_run_package_command`
    using :func:`asyncio.create_subprocess_exec` (no shell), **not**
    from this validator filtering characters. The only character
    filter we keep is newline / carriage-return because embedded
    newlines can still break argv logging and downstream tools that
    parse line-oriented output.
    """
    if not source or any(ch in source for ch in ("\n", "\r")):
        raise ValueError(f"install source {source!r} contains disallowed characters")

    # Windows drive-letter absolute path (``C:/...`` or ``C:\...``) detected
    # cross-platform: on non-Windows runners ``Path.is_absolute`` returns
    # False for these, and ``urlparse`` would otherwise read the drive
    # letter as a URL scheme and reject it.
    if len(source) >= 3 and source[1] == ":" and source[2] in ("/", "\\") and source[0].isalpha():
        return

    # Check filesystem paths BEFORE URL parsing: on Windows ``urlparse`` reads
    # ``C:\foo`` as scheme ``"c"``, which would otherwise be rejected below.
    # We require ``is_absolute()`` only — relative paths that happen to
    # exist on the caller's CWD must not sneak through, since the
    # docstring promises absolute-only.
    path = Path(source)
    if path.is_absolute():
        return

    parsed = urlparse(source)
    if parsed.scheme in {"https", "file"}:
        return
    if parsed.scheme:  # any other scheme (git, http plain, ssh, ...) is not allowed.
        raise ValueError(f"install source scheme {parsed.scheme!r} is not supported")

    # PyPI name-or-spec: at minimum must start with alnum.
    if not source[0].isalnum():
        raise ValueError(f"install source {source!r} is not a recognised PyPI spec")


async def _run_package_command(args: list[str]) -> None:
    """Run ``uv <args>`` (or plain ``<args>`` as fallback) in a subprocess.

    Uses :func:`asyncio.create_subprocess_exec` — no shell — so shell
    metacharacters in arguments cannot escape into the shell. Raises
    :class:`RuntimeError` with stderr on non-zero exit.

    When the caller coroutine is cancelled (or the process receives
    ``SIGINT`` while ``communicate()`` is awaiting), we must terminate
    the child before re-raising — :func:`asyncio.create_subprocess_exec`
    does NOT auto-kill the child on cancel, and ``pip`` / ``uv`` can
    happily continue downloading and mutating the user's environment
    for minutes after we let go (lesson #30).
    """
    uv = shutil.which("uv")
    if uv is not None:
        argv = [uv, *args]
    else:
        # Fall back to whatever tool name came in (pip / pip uninstall).
        resolved = shutil.which(args[0])
        if resolved is None:
            raise RuntimeError(f"neither 'uv' nor {args[0]!r} found on PATH")
        argv = [resolved, *args[1:]]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await proc.communicate()
    # PEP 758 (py3.12+) tuple-except without parens; ruff format normalizes
    # parenthesised form back to this under ``target-version = "py314"``
    # (lesson #16). Both catch the same BaseException subclasses.
    except asyncio.CancelledError, KeyboardInterrupt:
        # Ask politely, then force — never return until the child is
        # reaped so we do not leak a defunct-hunting zombie.
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(
            f"command {argv!r} failed with exit {proc.returncode}: {stderr.decode(errors='replace').strip()}"
        )


# ---------------------------------------------------------------------------
# on_event dispatch wrapper.
#
# The ``Plugin`` protocol's ``on_event`` takes ``(ev, ctx)``; the bus's
# handler signature is ``(ev,) -> Awaitable[None]``. The registry builds a
# per-plugin closure here that binds the KernelContext — one closure per
# plugin, shared across every kind that plugin subscribes to.
# ---------------------------------------------------------------------------


def _make_handler(
    plugin: Plugin,
    ctx: KernelContext,
    *,
    on_success: Callable[[], None],
) -> Callable[[Event], Awaitable[None]]:
    """Return an ``(ev) -> Awaitable[None]`` closure that forwards to on_event.

    ``on_success`` fires **only** if ``plugin.on_event`` returned without
    raising — the caller uses it to reset the plugin's consecutive
    failure counter so the threshold tracks consecutive errors, not
    cumulative.
    """

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)
        on_success()

    return _handler


__all__ = ["PluginRegistry", "PluginStatus", "validate_install_source"]
